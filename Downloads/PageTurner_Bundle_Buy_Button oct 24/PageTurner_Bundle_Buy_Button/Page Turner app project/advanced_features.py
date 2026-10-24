"""Add-on features for PageTurner. Uses Flask + Python standard library + SQLite only."""
import os, sqlite3, secrets, hashlib, hmac, struct, time, json, html
from datetime import datetime, timedelta, date
from flask import Blueprint, current_app, request, session, redirect, url_for, flash, abort, jsonify, make_response, render_template_string
from functools import wraps

bp = Blueprint('advanced', __name__)

def db():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database.db')
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init_advanced_db():
    c=db(); q=c.cursor()
    statements = [
    "CREATE TABLE IF NOT EXISTS book_bundles(id INTEGER PRIMARY KEY, name TEXT NOT NULL, price_paise INTEGER NOT NULL CHECK(price_paise>=0), active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS bundle_items(bundle_id INTEGER NOT NULL, book_id INTEGER NOT NULL, quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity>0), PRIMARY KEY(bundle_id,book_id), FOREIGN KEY(bundle_id) REFERENCES book_bundles(id) ON DELETE CASCADE, FOREIGN KEY(book_id) REFERENCES books(id))",
    "CREATE TABLE IF NOT EXISTS review_votes(user_id INTEGER NOT NULL, review_id INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,review_id))",
    "CREATE TABLE IF NOT EXISTS review_reports(id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'reported', created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(review_id,user_id))",
    "CREATE TABLE IF NOT EXISTS book_requests(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL COLLATE NOCASE, author TEXT NOT NULL DEFAULT '', request_count INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,title,author))",
    "CREATE TABLE IF NOT EXISTS wishlist_shares(user_id INTEGER PRIMARY KEY, token TEXT UNIQUE NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS price_history(id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, old_price INTEGER NOT NULL, new_price INTEGER NOT NULL, changed_by INTEGER, changed_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS checkout_tokens(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, order_id INTEGER, used INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS gift_cards(id INTEGER PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL, initial_paise INTEGER NOT NULL, expires_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS gift_card_ledger(id INTEGER PRIMARY KEY, gift_card_id INTEGER NOT NULL, amount_paise INTEGER NOT NULL, order_id INTEGER, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS backorders(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0), status TEXT NOT NULL DEFAULT 'waiting', created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS admin_audit_chain(id INTEGER PRIMARY KEY, content TEXT NOT NULL, previous_hash TEXT NOT NULL, row_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS notification_outbox(id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', last_error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS subscriptions(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, category TEXT NOT NULL, next_due TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(user_id,category))",
    "CREATE TABLE IF NOT EXISTS subscription_runs(id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL, due_month TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, order_id INTEGER, UNIQUE(subscription_id,due_month))",
    "CREATE TABLE IF NOT EXISTS totp_secrets(user_id INTEGER PRIMARY KEY, secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, last_counter INTEGER NOT NULL DEFAULT -1)",
    "CREATE TABLE IF NOT EXISTS api_keys(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, key_hash TEXT UNIQUE NOT NULL, scope TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS permissions(name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS role_permissions(role TEXT NOT NULL, permission TEXT NOT NULL, PRIMARY KEY(role,permission))",
    "CREATE TABLE IF NOT EXISTS pricing_rules(id INTEGER PRIMARY KEY, type TEXT NOT NULL, book_or_category TEXT NOT NULL, value INTEGER NOT NULL, priority INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS points_ledger(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, order_id INTEGER, points INTEGER NOT NULL, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS helpful_vote_guard(id INTEGER PRIMARY KEY, note TEXT)",
    ]
    for s in statements: q.execute(s)
    cols={r[1] for r in q.execute('PRAGMA table_info(books)')}
    if 'release_date' not in cols: q.execute('ALTER TABLE books ADD COLUMN release_date TEXT')
    if 'sale_price_paise' not in cols: q.execute('ALTER TABLE books ADD COLUMN sale_price_paise INTEGER')
    if 'sale_start' not in cols: q.execute('ALTER TABLE books ADD COLUMN sale_start TEXT')
    if 'sale_end' not in cols: q.execute('ALTER TABLE books ADD COLUMN sale_end TEXT')
    if 'version' not in cols: q.execute('ALTER TABLE books ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
    if 'hidden' not in {r[1] for r in q.execute('PRAGMA table_info(reviews)')}: q.execute('ALTER TABLE reviews ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0')
    c.commit(); c.close()

def login_required(f):
    @wraps(f)
    def w(*a,**kw):
        if not session.get('user_id'): return redirect(url_for('login'))
        return f(*a,**kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a,**kw):
        if not session.get('admin_logged_in'): abort(403)
        return f(*a,**kw)
    return w

def page(title, body):
    return render_template_string("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#172554"><title>{{title}} · PageTurner Books</title><style>
:root{--ink:#172554;--muted:#64748b;--paper:#f6f8fc;--line:#e2e8f0;--brand:#3157c8;--brand2:#1d3b91;--shadow:0 14px 40px rgba(23,37,84,.08)}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:#1e293b;font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--brand);text-underline-offset:3px}a:hover{color:var(--brand2)}
.sitebar{background:linear-gradient(120deg,#172554,#2946a3);color:#fff;padding:15px 20px;box-shadow:0 4px 20px #17255420}
.sitebar-inner,.wrap{width:min(1080px,calc(100% - 36px));margin:auto}.sitebar-inner{display:flex;justify-content:space-between;align-items:center;gap:16px}
.brand{font-weight:800;color:#fff;text-decoration:none;font-size:18px}.sitebar nav a{color:#e0e7ff}
.wrap{padding:30px 0 56px}.crumb{font-size:14px;color:var(--muted);margin-bottom:20px}.crumb a{font-weight:600}
.hero{background:linear-gradient(135deg,#fff,#eef2ff);border:1px solid #dce4ff;border-radius:22px;padding:clamp(24px,5vw,44px);box-shadow:var(--shadow);margin-bottom:24px}
.eyebrow{text-transform:uppercase;letter-spacing:1.8px;font-size:12px;font-weight:800;color:var(--brand);margin:0 0 8px}
.hero h1{font-size:clamp(30px,5vw,46px);line-height:1.12;letter-spacing:-1px;margin:0 0 12px;color:var(--ink)}
.lede{font-size:17px;color:#475569;max-width:700px;margin:0}.stats{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}
.stat{background:#fff;border:1px solid var(--line);border-radius:13px;padding:10px 15px;min-width:130px}.stat strong{display:block;color:var(--ink);font-size:21px}.stat span{font-size:12px;color:var(--muted)}
.section{margin:26px 0}.section-head{display:flex;align-items:end;justify-content:space-between;gap:12px;margin-bottom:14px}
.section h2{font-size:23px;line-height:1.25;margin:0;color:var(--ink)}.section-sub{color:var(--muted);font-size:14px;margin:5px 0 0}
.bio-card,.book-card,.feature-card{background:#fff;border:1px solid var(--line);border-radius:17px;padding:22px;box-shadow:0 5px 18px rgba(23,37,84,.04)}
.bio-card p{margin:0 0 12px}.bio-card p:last-child{margin-bottom:0}
.book-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}
.book-card{display:flex;flex-direction:column;gap:9px;transition:transform .18s,box-shadow .18s}.book-card:hover{transform:translateY(-3px);box-shadow:var(--shadow)}
.book-title{font-weight:800;font-size:18px;line-height:1.35;color:var(--ink);text-decoration:none}.book-meta{font-size:13px;color:var(--muted)}
.book-bottom{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:auto;padding-top:12px}.price{font-weight:800;color:var(--ink);font-size:18px}
.pill{display:inline-flex;align-items:center;border-radius:999px;padding:4px 10px;background:#ecfdf5;color:#047857;font-size:12px;font-weight:800}.pill.out{background:#fff7ed;color:#c2410c}
.btn{display:inline-flex;justify-content:center;align-items:center;border:0;border-radius:10px;background:var(--brand);color:#fff;padding:10px 14px;text-decoration:none;font-weight:700;cursor:pointer}.btn:hover{background:var(--brand2);color:#fff}
.btn-secondary{background:#eef2ff;color:var(--brand2)}.btn-secondary:hover{background:#dbeafe;color:var(--brand2)}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}.feature-card{display:block;text-decoration:none;color:var(--ink);font-weight:750}.feature-card span{display:block;font-weight:400;color:var(--muted);font-size:13px;margin-top:3px}
input,button,select,textarea{font:inherit;padding:11px 13px;border:1px solid #cbd5e1;border-radius:10px;max-width:100%}button{background:var(--brand);color:#fff;font-weight:700;cursor:pointer}
table{border-collapse:collapse;width:100%;background:#fff}td,th{padding:12px;border-bottom:1px solid var(--line);text-align:left}th{background:#eef2ff;color:var(--ink)}
.footer{text-align:center;color:var(--muted);font-size:13px;padding:20px;border-top:1px solid var(--line)}
:focus-visible{outline:3px solid #f5b942;outline-offset:3px}
@media(max-width:600px){.sitebar-inner{align-items:flex-start;flex-direction:column}.wrap{padding-top:22px}.hero{border-radius:16px}.section-head{align-items:flex-start;flex-direction:column}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><header class="sitebar"><div class="sitebar-inner"><a class="brand" href="/">📚 PageTurner Books</a><nav><a href="/">Store home</a> &nbsp;·&nbsp; <a href="/advanced">Explore features</a></nav></div></header><main class="wrap"><div class="crumb"><a href="/">← Store home</a> &nbsp;/&nbsp; <a href="/advanced">Explore features</a></div>{{body|safe}}</main><footer class="footer">Find your next favourite. Read more with PageTurner Books.</footer></body></html>""", title=title, body=body)

@bp.route('/advanced')
def hub():
    features=[('/recently-viewed','Recently viewed','Pick up where you left off.'),('/compare','Compare books','Compare two titles side by side.'),('/request-book','Request a book','Tell us what you want to read next.'),('/wishlist/share','Share wishlist','Create or revoke a sharing link.'),('/account/export','Download my data','Export your account information.'),('/account/delete','Delete my account','Manage your account privacy.'),('/admin/advanced','Advanced admin tools','Store management utilities.'),('/api/books?after=0&limit=10','Books API pagination','Browse catalogue data in pages.')]
    cards=''.join(f'<a class="feature-card" href="{u}">{t}<span>{d} →</span></a>' for u,t,d in features)
    body=f'<section class="hero"><p class="eyebrow">Your reading toolkit</p><h1>Explore PageTurner</h1><p class="lede">Useful tools to discover books, manage your reading list and make your store experience yours.</p></section><section class="section"><div class="section-head"><div><h2>Quick actions</h2><p class="section-sub">Choose a tool to get started.</p></div></div><div class="feature-grid">{cards}</div></section>'
    return page('PageTurner advanced features',body)

@bp.route('/book/<int:book_id>/view-event')
def view_event(book_id):
    c=db(); exists=c.execute('SELECT id FROM books WHERE id=?',(book_id,)).fetchone(); c.close()
    if not exists: abort(404)
    ids=session.get('recent_books',[]); ids=[x for x in ids if x!=book_id]; ids.insert(0,book_id); session['recent_books']=ids[:5]
    return redirect(url_for('book_detail',book_id=book_id))

@bp.route('/recently-viewed')
def recently_viewed():
    ids=session.get('recent_books',[]); c=db(); rows=[]
    for bid in ids:
        r=c.execute('SELECT id,title,author,price,rating,stock FROM books WHERE id=?',(bid,)).fetchone()
        if r: rows.append(r)
    c.close(); body='<ul>'+''.join(f"<li><a href='/book/{r['id']}'>{r['title']}</a> — {r['author']} — ₹{r['price']//100}</li>" for r in rows)+'</ul>'
    return page('Recently viewed books',body or '<p>No books viewed yet. Open a book via its “View” link to track it.</p>')

@bp.route('/author/<path:name>')
def author_page(name):
    c=db()
    rows=c.execute('SELECT id,title,author,price,rating,stock FROM books WHERE author=? COLLATE NOCASE ORDER BY title COLLATE NOCASE',(name,)).fetchall()
    c.close()
    if not rows: abort(404)
    raw_author=rows[0]['author'] or name
    author_name=html.escape(str(raw_author))
    bios={
      'harper lee': "Nelle Harper Lee (1926–2016) was an American novelist best known for <em>To Kill a Mockingbird</em>. Published in 1960, the novel explores childhood, empathy, justice and moral courage in the American South. It won the Pulitzer Prize for Fiction in 1961 and became a landmark of modern American literature."
    }
    story=bios.get(str(raw_author).strip().casefold(), "Discover this author's writing through the titles collected below. This profile brings together the books currently available in PageTurner Books. An expanded biography can be added when verified editorial information is available.")
    cards=[]
    for r in rows:
        book_title=html.escape(str(r['title'] or 'Untitled'))
        stock=int(r['stock'] or 0)
        price=int(r['price'] or 0)
        availability='<span class="pill">In stock</span>' if stock>0 else '<span class="pill out">Currently unavailable</span>'
        rating='★ '+html.escape(str(r['rating'])) if r['rating'] is not None else 'Reader favourite'
        bid=int(r['id'])
        cards.append(f'<article class="book-card"><div class="book-meta">{rating}</div><a class="book-title" href="/book/{bid}">{book_title}</a><div class="book-meta">By {author_name}</div><div class="book-bottom"><span class="price">₹{price/100:.2f}</span>{availability}</div><a class="btn btn-secondary" href="/book/{bid}">View book →</a></article>')
    available=sum(1 for r in rows if int(r['stock'] or 0)>0)
    body=(f'<section class="hero"><p class="eyebrow">Author spotlight</p><h1>{author_name}</h1><p class="lede">Explore the books and stories connected with {author_name}.</p><div class="stats"><div class="stat"><strong>{len(rows)}</strong><span>book(s) in our store</span></div><div class="stat"><strong>{available}</strong><span>available now</span></div><div class="stat"><strong>Author</strong><span>profile & reading list</span></div></div></section>'
          f'<section class="section"><div class="section-head"><div><h2>About the author</h2><p class="section-sub">A little context before you choose your next read.</p></div></div><div class="bio-card"><p>{story}</p></div></section>'
          f'<section class="section"><div class="section-head"><div><h2>Books by {author_name}</h2><p class="section-sub">Browse the titles currently listed in our store.</p></div><span class="pill">{len(rows)} title(s)</span></div><div class="book-grid">{"".join(cards)}</div></section>'
          '<section class="section"><a class="btn btn-secondary" href="/">← Back to all books</a></section>')
    return page('Author: '+str(raw_author),body)

@bp.route('/compare', methods=['GET','POST'])
def compare():
    ids=request.values.getlist('book_id',type=int)
    if request.method=='POST' and not ids:
        raw=request.form.get('ids',''); ids=[]
        try: ids=[int(x) for x in raw.split(',') if x.strip()]
        except ValueError: ids=[]
    if not ids:
        c=db(); books=c.execute('SELECT id,title,author,price,rating,stock FROM books ORDER BY title').fetchall(); c.close()
        body="<form method=post><p>Select exactly two book IDs separated by a comma:</p><input name=ids placeholder='1,2' required><button>Compare</button></form><ul>"+''.join(f"<li>{b['id']}: {b['title']} — {b['author']}</li>" for b in books)+'</ul>'
        return page('Compare two books',body)
    if len(ids)!=2: return page('Compare two books','<p>Please choose exactly two books. One or three books cannot be compared.</p><a href="/compare">Try again</a>'),400
    c=db(); rows=c.execute('SELECT id,title,author,price,rating,stock FROM books WHERE id IN (?,?)',ids).fetchall(); c.close()
    if len(rows)!=2: abort(404)
    return page('Compare two books','<table><tr><th>Field</th>'+''.join(f"<th>{r['title']}</th>" for r in rows)+'</tr>'+''.join(f"<tr><th>{field}</th>"+''.join(f'<td>{r[field]}</td>' for r in rows)+'</tr>' for field in ['author','price','rating','stock'])+'</table>')

@bp.route('/request-book',methods=['GET','POST'])
@login_required
def request_book():
    if request.method=='POST':
        title=request.form.get('title','').strip()[:200]; author=request.form.get('author','').strip()[:160]
        if not title: return page('Request a book','<p>Title is required.</p>'),400
        c=db(); r=c.execute('SELECT id FROM book_requests WHERE user_id=? AND lower(title)=lower(?) AND lower(author)=lower(?)',(session['user_id'],title,author)).fetchone()
        if r: c.execute('UPDATE book_requests SET request_count=request_count+1 WHERE id=?',(r['id'],))
        else: c.execute('INSERT INTO book_requests(user_id,title,author) VALUES(?,?,?)',(session['user_id'],title,author))
        c.commit(); c.close(); flash('Book request saved.'); return redirect('/request-book')
    c=db(); rows=c.execute('SELECT title,author,SUM(request_count) count FROM book_requests GROUP BY lower(title),lower(author) ORDER BY count DESC,title').fetchall(); c.close()
    body="<form method=post><input name=title placeholder='Book title' required><input name=author placeholder='Author'><button>Request</button></form><h2>Most requested</h2><ul>"+''.join(f"<li>{r['title']} — {r['author']} ({r['count']} requests)</li>" for r in rows)+'</ul>'
    return page('Request a book',body)

@bp.route('/wishlist/share',methods=['GET','POST'])
@login_required
def share_wishlist():
    c=db(); user=session['user_id']
    if request.method=='POST':
        action=request.form.get('action')
        if action=='off': c.execute('UPDATE wishlist_shares SET active=0 WHERE user_id=?',(user,)); c.commit(); c.close(); return page('Share wishlist','<p>Public link disabled.</p>')
        token=secrets.token_urlsafe(32); c.execute('INSERT INTO wishlist_shares(user_id,token,active) VALUES(?,?,1) ON CONFLICT(user_id) DO UPDATE SET token=excluded.token,active=1',(user,token)); c.commit(); c.close()
        return page('Share wishlist',f"<p>Your read-only link: <a href='/w/{token}'>/w/{token}</a></p><form method=post><button name=action value=off>Turn link off</button></form>")
    row=c.execute('SELECT token,active FROM wishlist_shares WHERE user_id=?',(user,)).fetchone(); c.close()
    body="<form method=post><button>Create / refresh public link</button></form>"
    if row and row['active']: body+=f"<p>Current link: <a href='/w/{row['token']}'>/w/{row['token']}</a></p><form method=post><button name=action value=off>Turn link off</button></form>"
    return page('Share my wishlist',body)

@bp.route('/w/<token>')
def public_wishlist(token):
    c=db(); row=c.execute('SELECT user_id,active FROM wishlist_shares WHERE token=?',(token,)).fetchone()
    if not row or not row['active']: c.close(); abort(404)
    books=c.execute('SELECT b.id,b.title,b.author,b.price FROM wishlist w JOIN books b ON b.id=w.book_id WHERE w.user_id=?',(row['user_id'],)).fetchall(); c.close()
    return page('Shared wishlist','<ul>'+''.join(f"<li>{b['title']} — {b['author']} — ₹{b['price']//100}</li>" for b in books)+'</ul>')

@bp.route('/account/export')
@login_required
def export_data():
    c=db(); uid=session['user_id']; user=c.execute('SELECT id,username,name,email FROM users WHERE id=?',(uid,)).fetchone(); orders=c.execute('SELECT id,total_amount,status,created_at FROM orders WHERE user_id=?',(uid,)).fetchall(); items=[]
    for o in orders: items.extend([dict(x) for x in c.execute('SELECT order_id,book_id,quantity,price_at_purchase FROM order_items WHERE order_id=?',(o['id'],)).fetchall()])
    c.close(); resp=make_response(json.dumps({'profile':dict(user) if user else {},'orders':[dict(o) for o in orders],'order_items':items},default=str,indent=2)); resp.headers['Content-Type']='application/json'; resp.headers['Content-Disposition']='attachment; filename=my-pageturner-data.json'; return resp

@bp.route('/account/delete',methods=['GET','POST'])
@login_required
def delete_account():
    if request.method=='POST':
        from werkzeug.security import check_password_hash
        c=db(); u=c.execute('SELECT password FROM users WHERE id=?',(session['user_id'],)).fetchone(); pw=request.form.get('password','')
        if not u or not check_password_hash(u['password'] or '',pw): c.close(); return page('Delete account','<p>Password incorrect.</p>'),400
        uid=session['user_id']; c.execute("UPDATE users SET name='Deleted user',username='deleted-'||id,email='deleted+'||id||'@invalid.local',password=? WHERE id=?",(secrets.token_hex(32),uid)); c.commit(); c.close(); session.clear(); return page('Account deleted','<p>Your account has been anonymised. Order records are retained for accounting.</p>')
    return page('Delete my account',"<form method=post><label>Confirm password <input type=password name=password required></label><button>Delete / anonymise account</button></form>")

@bp.route('/api/books')
def api_books_keyset():
    after=max(0,request.args.get('after',0,type=int)); limit=min(50,max(1,request.args.get('limit',10,type=int))); c=db(); rows=c.execute('SELECT id,title,author,price,rating,stock FROM books WHERE id>? ORDER BY id LIMIT ?',(after,limit)).fetchall(); c.close()
    return jsonify({'books':[dict(r) for r in rows],'next_cursor':rows[-1]['id'] if len(rows)==limit else None})

@bp.route('/admin/advanced',methods=['GET','POST'])
@admin_required
def admin_advanced():
    c=db(); msg=''
    if request.method=='POST':
        action=request.form.get('action')
        try:
            if action=='price':
                bid=int(request.form['book_id']); new=int(request.form['price']); old=c.execute('SELECT price FROM books WHERE id=?',(bid,)).fetchone()
                if old and new>=0:
                    c.execute('INSERT INTO price_history(book_id,old_price,new_price,changed_by) VALUES(?,?,?,?)',(bid,old['price'],new,session.get('user_id'))); c.execute('UPDATE books SET price=?,version=version+1 WHERE id=?',(new,bid)); msg='Price updated and history recorded.'
            elif action=='release':
                c.execute('UPDATE books SET release_date=? WHERE id=?',(request.form.get('release_date') or None,int(request.form['book_id']))); msg='Release date updated.'
            elif action=='rule':
                c.execute('INSERT INTO pricing_rules(type,book_or_category,value,priority,active) VALUES(?,?,?,?,1)',(request.form['type'],request.form['target'],int(request.form['value']),int(request.form.get('priority',100)))); msg='Pricing rule created.'
            elif action=='bundle':
                name=request.form['name'].strip()
                price=int(request.form['price'])
                ids=list(dict.fromkeys(int(x.strip()) for x in request.form['book_ids'].split(',') if x.strip()))
                if not name or price < 0 or not ids:
                    raise ValueError('Enter a bundle name, a non-negative price, and at least one book ID.')
                existing={r['id'] for r in c.execute('SELECT id FROM books WHERE id IN (%s)' % ','.join('?' for _ in ids), ids).fetchall()}
                if existing != set(ids):
                    raise ValueError('One or more book IDs do not exist.')
                cur=c.execute('INSERT INTO book_bundles(name,price_paise) VALUES(?,?)',(name,price))
                bid=cur.lastrowid
                for book_id in ids:
                    c.execute('INSERT INTO bundle_items(bundle_id,book_id,quantity) VALUES(?,?,1)',(bid,book_id))
                msg='Bundle created and published to the customer storefront.'
            elif action=='edit_bundle':
                bid=int(request.form['bundle_id'])
                name=request.form['name'].strip()
                price=int(request.form['price'])
                ids=list(dict.fromkeys(int(x.strip()) for x in request.form['book_ids'].split(',') if x.strip()))
                if not name or price < 0 or not ids:
                    raise ValueError('Enter a bundle name, a non-negative price, and at least one book ID.')
                exists=c.execute('SELECT id FROM book_bundles WHERE id=?',(bid,)).fetchone()
                if not exists:
                    raise ValueError('Bundle not found.')
                placeholders=','.join('?' for _ in ids)
                found={r['id'] for r in c.execute('SELECT id FROM books WHERE id IN ('+placeholders+')',ids).fetchall()}
                if found != set(ids):
                    raise ValueError('One or more book IDs do not exist.')
                c.execute('UPDATE book_bundles SET name=?,price_paise=?,active=1 WHERE id=?',(name,price,bid))
                c.execute('DELETE FROM bundle_items WHERE bundle_id=?',(bid,))
                for book_id in ids:
                    c.execute('INSERT INTO bundle_items(bundle_id,book_id,quantity) VALUES(?,?,1)',(bid,book_id))
                msg='Bundle updated and published to the customer storefront.'
            elif action=='deactivate_bundle':
                bid=int(request.form['bundle_id'])
                c.execute('UPDATE book_bundles SET active=0 WHERE id=?',(bid,))
                msg='Bundle hidden from the customer storefront.'
            elif action=='bulk_increase':
                pct=int(request.form.get('percent',10)); ids=[int(x) for x in request.form['book_ids'].split(',') if x.strip()]
                c.execute('BEGIN IMMEDIATE')
                for bid in ids:
                    r=c.execute('SELECT price FROM books WHERE id=?',(bid,)).fetchone()
                    if r:
                        new=(int(r['price'])*(100+pct)+50)//100; c.execute('INSERT INTO price_history(book_id,old_price,new_price,changed_by) VALUES(?,?,?,?)',(bid,r['price'],new,session.get('user_id'))); c.execute('UPDATE books SET price=?,version=version+1 WHERE id=?',(new,bid))
                msg='Bulk price update completed atomically.'
            c.commit()
        except Exception as e: c.rollback(); msg='Update failed: '+str(e)
    books=c.execute('SELECT id,title,price,release_date,version FROM books ORDER BY title').fetchall()
    bundles=[dict(r) for r in c.execute('SELECT id,name,price_paise,active FROM book_bundles ORDER BY id DESC').fetchall()]
    for bundle in bundles:
        bundle['book_ids'] = ','.join(str(r['book_id']) for r in c.execute('SELECT book_id FROM bundle_items WHERE bundle_id=? ORDER BY book_id',(bundle['id'],)).fetchall())
    history=c.execute('SELECT h.*,b.title FROM price_history h JOIN books b ON b.id=h.book_id ORDER BY h.id DESC LIMIT 30').fetchall(); c.close()
    body='<p>'+msg+'</p><h2>Set release date / price</h2><form method=post><input type=hidden name=action value=release><select name=book_id>'+''.join(f"<option value='{b['id']}'>{b['title']}</option>" for b in books)+'</select><input name=release_date type=date><button>Save release date</button></form>'
    body+='<form method=post><input type=hidden name=action value=price><select name=book_id>'+''.join(f"<option value='{b['id']}'>{b['title']}</option>" for b in books)+'</select><input type=number name=price min=0 placeholder="Price in paise" required><button>Set price</button></form>'
    body+='<h2>Bulk increase</h2><form method=post><input type=hidden name=action value=bulk_increase><input name=book_ids placeholder="Book IDs: 1,2,3" required><input name=percent type=number value=10><button>Increase selected prices</button></form>'
    body+='<section class="admin-bundle-panel"><h2>Create bundle</h2><p>Create or edit a combo here. Prices are entered in paise (₹799 = 79900 paise). Active bundles with valid books appear on the customer home page.</p><form method=post><input type=hidden name=action value=bundle><input name=name placeholder="Bundle name" required><input name=price type=number min=0 placeholder="Price in paise" required><input name=book_ids placeholder="Book IDs: 1,2" required><button>Create and publish bundle</button></form></section>'
    body+='<h2>Pricing rule</h2><form method=post><input type=hidden name=action value=rule><select name=type><option value=category_percent>Category % off</option><option value=buy2get1>Buy 2 get 1 free</option></select><input name=target placeholder="Category or *" required><input name=value type=number value=10><input name=priority type=number value=100><button>Add rule</button></form>'
    body+='<style>.admin-bundle-panel{background:#f8faff;border:1px solid #dbe5ff;border-radius:14px;padding:18px;margin:20px 0}.bundle-edit-list{display:grid;gap:14px;margin:14px 0}.bundle-edit-card{background:white;border:1px solid #dbe2ef;border-radius:12px;padding:16px}.bundle-edit-card h3{margin:0 0 10px}.bundle-edit-card form{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.bundle-edit-card input{min-width:150px;flex:1}.bundle-edit-card button{margin:0}.bundle-status{font-size:12px;color:#64748b}.danger-button{background:#9f1239}</style>'
    body+='<h2>Manage bundles</h2><p>Edit the name, price, and included book IDs. Use the IDs shown in the book list/database. Deactivated bundles are hidden from customers.</p><div class="bundle-edit-list">'
    for b in bundles:
        status='Published to customers' if b['active'] else 'Hidden from customers'
        body+=f"<article class='bundle-edit-card'><h3>{html.escape(str(b['name']))} <span class='bundle-status'>· {status}</span></h3><form method='post'><input type='hidden' name='action' value='edit_bundle'><input type='hidden' name='bundle_id' value='{b['id']}'><label>Bundle name <input name='name' value='{html.escape(str(b['name']), quote=True)}' required></label><label>Price (paise) <input type='number' min='0' name='price' value='{int(b['price_paise'])}' required></label><label>Book IDs <input name='book_ids' value='{html.escape(str(b['book_ids']), quote=True)}' required></label><button>Save changes & publish</button></form>"
        if b['active']:
            body+=f"<form method='post' onsubmit=\"return confirm('Hide this bundle from customers?')\"><input type='hidden' name='action' value='deactivate_bundle'><input type='hidden' name='bundle_id' value='{b['id']}'><button class='danger-button'>Hide from customers</button></form>"
        body+='</article>'
    body+='</div><h2>Recent price history</h2><table><tr><th>Book</th><th>Old paise</th><th>New paise</th><th>When</th></tr>'+''.join(f"<tr><td>{h['title']}</td><td>{h['old_price']}</td><td>{h['new_price']}</td><td>{h['changed_at']}</td></tr>" for h in history)+'</table>'
    return page('Advanced admin tools',body)

@bp.route('/book/<int:book_id>/optimistic-edit',methods=['GET','POST'])
@admin_required
def optimistic_edit(book_id):
    c=db(); b=c.execute('SELECT * FROM books WHERE id=?',(book_id,)).fetchone()
    if not b: c.close(); abort(404)
    if request.method=='POST':
        title=request.form.get('title','').strip(); version=int(request.form.get('version',-1)); cur=c.execute('UPDATE books SET title=?,version=version+1 WHERE id=? AND version=?',(title,book_id,version))
        if cur.rowcount!=1: c.close(); return page('Edit book','<p>Conflict: another admin changed this book. Reload before saving.</p>'),409
        c.commit(); c.close(); return page('Edit book','<p>Book saved.</p>')
    body=f"<form method=post><input name=title value='{b['title']}' required><input type=hidden name=version value='{b['version']}'><button>Save version {b['version']}</button></form>"; c.close(); return page('Edit book',body)

@bp.route('/admin/audit/verify')
@admin_required
def verify_audit():
    c=db(); rows=c.execute('SELECT * FROM admin_audit_chain ORDER BY id').fetchall(); prev='GENESIS'; broken=[]
    for r in rows:
        expected=hashlib.sha256((prev+r['content']).encode()).hexdigest()
        if r['previous_hash']!=prev or r['row_hash']!=expected: broken.append(r['id'])
        prev=r['row_hash']
    c.close(); return page('Verify audit log',f"<p>{'Chain valid.' if not broken else 'Broken rows: '+', '.join(map(str,broken))}</p>")

@bp.route('/admin/outbox/run',methods=['POST'])
@admin_required
def run_outbox():
    c=db(); rows=c.execute("SELECT * FROM notification_outbox WHERE status='pending' AND attempts<3 ORDER BY id").fetchall(); sent=0
    for r in rows:
        try:
            print('OUTBOX:',r['payload']); c.execute("UPDATE notification_outbox SET status='sent',attempts=attempts+1,last_error=NULL WHERE id=? AND status='pending'",(r['id'],)); sent+=1
        except Exception as e:
            c.execute("UPDATE notification_outbox SET attempts=attempts+1,last_error=? WHERE id=?",(str(e),r['id']))
    c.commit(); c.close(); return page('Notification outbox',f'<p>Processed {sent} pending notification(s). Messages are printed to the console.</p>')

@bp.route('/admin/reports/growth')
@admin_required
def growth_report():
    c=db()
    try: rows=c.execute("SELECT strftime('%Y-%m',created_at) month,SUM(total_amount) revenue,LAG(SUM(total_amount)) OVER (ORDER BY strftime('%Y-%m',created_at)) previous,SUM(SUM(total_amount)) OVER (ORDER BY strftime('%Y-%m',created_at)) running FROM orders GROUP BY month ORDER BY month").fetchall()
    except sqlite3.OperationalError: rows=[]
    c.close(); body='<table><tr><th>Month</th><th>Revenue paise</th><th>Change %</th><th>Running total</th></tr>'+''.join(f"<tr><td>{r['month']}</td><td>{r['revenue'] or 0}</td><td>{'' if not r['previous'] else round((r['revenue']-r['previous'])*100/r['previous'],2)}</td><td>{r['running']}</td></tr>" for r in rows)+'</table>'; return page('Monthly growth report',body)

@bp.route('/admin/sales-chart')
@admin_required
def sales_chart():
    c=db(); rows=c.execute("SELECT date(created_at) day,COALESCE(SUM(total_amount),0) revenue FROM orders WHERE date(created_at)>=date('now','-6 days') GROUP BY date(created_at)").fetchall(); values={r['day']:r['revenue'] for r in rows}; days=[(date.today()-timedelta(days=i)).isoformat() for i in range(6,-1,-1)]; mx=max(values.values() or [0]) or 1; body=''.join(f"<div style='display:inline-block;vertical-align:bottom;width:12%;margin:1%;text-align:center'><div style='height:{max(1,int(values.get(d,0)*160/mx))}px;background:#4787e8'></div><small>{d[5:]}</small><br><small>{values.get(d,0)}</small></div>" for d in days); c.close(); return page('Last 7 days sales',body)

@bp.route('/admin/book-requests')
@admin_required
def admin_book_requests():
    c=db(); rows=c.execute('SELECT title,author,SUM(request_count) count FROM book_requests GROUP BY lower(title),lower(author) ORDER BY count DESC').fetchall(); c.close(); return page('Book requests','<ul>'+''.join(f"<li>{r['title']} — {r['author']} ({r['count']})</li>" for r in rows)+'</ul>')

@bp.route('/api/key/create',methods=['POST'])
@login_required
def create_api_key():
    scope=request.form.get('scope','read_books')
    if scope not in ('read_books','read_orders'): abort(400)
    raw=secrets.token_urlsafe(32); digest=hashlib.sha256(raw.encode()).hexdigest(); c=db(); c.execute('INSERT INTO api_keys(user_id,key_hash,scope) VALUES(?,?,?)',(session['user_id'],digest,scope)); c.commit(); c.close(); return page('API key created',f'<p>Copy this key now; it will not be shown again:</p><code>{raw}</code><p>Scope: {scope}</p>')

@bp.route('/api/secure/books')
def secure_books():
    raw=request.headers.get('X-API-Key',''); digest=hashlib.sha256(raw.encode()).hexdigest() if raw else ''; c=db(); key=c.execute('SELECT scope FROM api_keys WHERE key_hash=? AND active=1',(digest,)).fetchone()
    if not key or key['scope']!='read_books': c.close(); return jsonify({'error':'API key missing or scope forbidden'}),403
    rows=c.execute('SELECT id,title,author,price,stock FROM books ORDER BY id').fetchall(); c.close(); return jsonify([dict(r) for r in rows])

@bp.route('/admin/subscriptions/run',methods=['POST'])
@admin_required
def run_subscriptions():
    c=db(); today=date.today().isoformat(); subs=c.execute('SELECT * FROM subscriptions WHERE active=1 AND next_due<=?',(today,)).fetchall(); made=0
    for s in subs:
        month=today[:7]
        try:
            c.execute('INSERT INTO subscription_runs(subscription_id,due_month,status) VALUES(?,?,?)',(s['id'],month,'skipped'))
            c.execute("UPDATE subscription_runs SET reason='Subscription queued for admin review' WHERE subscription_id=? AND due_month=?",(s['id'],month))
            c.execute("UPDATE subscriptions SET next_due=date(next_due,'+1 month') WHERE id=?",(s['id'],)); made+=1
        except sqlite3.IntegrityError: pass
    c.commit(); c.close(); return page('Run due subscriptions',f'<p>Processed {made} due subscription(s). Review skipped reasons in subscription_runs.</p>')

@bp.route('/admin/backorders/restock',methods=['POST'])
@admin_required
def fulfill_backorders():
    bid=request.form.get('book_id',type=int); qty=request.form.get('quantity',type=int)
    if not bid or not qty or qty<1: abort(400)
    c=db(); c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE books SET stock=stock+? WHERE id=?',(qty,bid)); available=qty; served=0
    for r in c.execute("SELECT * FROM backorders WHERE book_id=? AND status='waiting' ORDER BY id",(bid,)).fetchall():
        if available<r['quantity']: break
        available-=r['quantity']; served+=1; c.execute("UPDATE backorders SET status='fulfilled' WHERE id=?",(r['id'],))
    c.commit(); c.close(); return page('Backorders',f'<p>Restocked book {bid}; marked {served} oldest eligible backorder(s) fulfilled. Admin must confirm physical allocation before shipment.</p>')

@bp.route('/admin/reviews/<int:review_id>/moderate',methods=['POST'])
@admin_required
def moderate_review(review_id):
    action=request.form.get('action'); c=db(); c.execute('UPDATE reviews SET hidden=? WHERE id=?',(1 if action=='hide' else 0,review_id)); c.execute("UPDATE review_reports SET status=? WHERE review_id=?",('hidden' if action=='hide' else 'kept',review_id)); c.commit(); c.close(); return redirect('/admin/reviews')

@bp.route('/review/<int:review_id>/helpful',methods=['POST'])
@login_required
def helpful_vote(review_id):
    c=db()
    try: c.execute('INSERT INTO review_votes(user_id,review_id) VALUES(?,?)',(session['user_id'],review_id)); c.commit()
    except sqlite3.IntegrityError: pass
    count=c.execute('SELECT COUNT(*) n FROM review_votes WHERE review_id=?',(review_id,)).fetchone()['n']; c.close(); return jsonify({'helpful':count})

@bp.route('/review/<int:review_id>/report',methods=['POST'])
@login_required
def report_review(review_id):
    reason=request.form.get('reason','').strip()[:500]
    if not reason: abort(400)
    c=db()
    try: c.execute('INSERT INTO review_reports(review_id,user_id,reason) VALUES(?,?,?)',(review_id,session['user_id'],reason)); c.commit()
    except sqlite3.IntegrityError: pass
    c.close(); flash('Review reported to the administrator.'); return redirect(request.referrer or url_for('home'))

@bp.route('/admin/reports/reviews')
@admin_required
def reported_reviews():
    c=db(); rows=c.execute("SELECT rr.id,rr.review_id,rr.reason,rr.status,r.name,r.book_title,r.comment FROM review_reports rr JOIN reviews r ON r.id=rr.review_id WHERE rr.status='reported' ORDER BY rr.id").fetchall(); c.close()
    body=''.join(f"<div class=card><b>{r['name']} — {r['book_title']}</b><p>{r['comment']}</p><p>Report: {r['reason']}</p><form method=post action='/admin/reviews/{r['review_id']}/moderate'><button name=action value=hide>Hide</button><button name=action value=keep>Keep</button></form></div>" for r in rows); return page('Reported reviews',body or '<p>No reported reviews.</p>')

@bp.route('/account/totp/setup',methods=['GET','POST'])
@login_required
def totp_setup():
    def b32decode(s): return __import__('base64').b32decode(s+'='*((8-len(s)%8)%8))
    c=db(); row=c.execute('SELECT secret,enabled,last_counter FROM totp_secrets WHERE user_id=?',(session['user_id'],)).fetchone()
    if request.method=='POST':
        code=request.form.get('code',''); secret=row['secret'] if row else ''
        if not secret: c.close(); abort(400)
        counter=int(time.time()//30); ok=False; matched=None
        for step in (counter-1,counter,counter+1):
            digest=hmac.new(b32decode(secret),struct.pack('>Q',step),hashlib.sha1).digest(); off=digest[-1]&15; num=(struct.unpack('>I',digest[off:off+4])[0]&0x7fffffff)%1000000
            if hmac.compare_digest(f'{num:06d}',code): ok=True; matched=step
        if ok and matched>row.get('last_counter',-1):
            c.execute('UPDATE totp_secrets SET enabled=1,last_counter=? WHERE user_id=?',(matched,session['user_id'])); c.commit(); c.close(); return page('Two-factor login','<p>Two-factor code verified and enabled. Secret will not be shown again.</p>')
        c.close(); return page('Two-factor login','<p>Invalid or already-used code.</p>'),400
    if not row:
        secret=__import__('base64').b32encode(secrets.token_bytes(20)).decode().rstrip('='); c.execute('INSERT INTO totp_secrets(user_id,secret) VALUES(?,?)',(session['user_id'],secret)); c.commit(); row={'secret':secret,'enabled':0}
    c.close(); body=f"<p>Authenticator secret (shown only during setup): <code>{row['secret']}</code></p><p>Enter the current six-digit code from your authenticator app to enable.</p><form method=post><input name=code pattern='[0-9]{{6}}' maxlength=6 required><button>Verify and enable</button></form>" if not row['enabled'] else '<p>Two-factor is enabled.</p>'; return page('Two-factor login',body)

@bp.route('/admin/price-history/<int:book_id>')
@admin_required
def book_price_history(book_id):
    c=db(); rows=c.execute('SELECT * FROM price_history WHERE book_id=? ORDER BY id DESC',(book_id,)).fetchall(); c.close(); return page('Price history','<table><tr><th>Old</th><th>New</th><th>Changed</th></tr>'+''.join(f"<tr><td>{r['old_price']}</td><td>{r['new_price']}</td><td>{r['changed_at']}</td></tr>" for r in rows)+'</table>')

@bp.route('/admin/permissions')
@admin_required
def permission_page():
    c=db(); rows=c.execute('SELECT * FROM permissions ORDER BY name').fetchall(); c.close(); return page('Permissions','<p>Permission storage is ready. Use role_permissions to grant or revoke a named permission.</p><ul>'+''.join(f"<li>{r['name']}: {r['description']}</li>" for r in rows)+'</ul>')

@bp.route('/admin/loyalty')
@admin_required
def loyalty_admin():
    c=db(); rows=c.execute('SELECT user_id,SUM(points) balance FROM points_ledger GROUP BY user_id ORDER BY balance DESC').fetchall(); c.close(); return page('Loyalty points','<p>Earn: 1 point per ₹100 spent. Redeem: 100 points = ₹10.</p><ul>'+''.join(f"<li>User {r['user_id']}: {r['balance']} points</li>" for r in rows)+'</ul>')

# Additional safe entry points for checkout token issuance and gift-card ledger inspection.
@bp.route('/checkout/token')
@login_required
def checkout_token():
    token=secrets.token_urlsafe(32); c=db(); c.execute('INSERT INTO checkout_tokens(token,user_id) VALUES(?,?)',(token,session['user_id'])); c.commit(); c.close(); session['checkout_token']=token; return jsonify({'checkout_token':token})

@bp.route('/admin/gift-cards')
@admin_required
def gift_cards_admin():
    c=db(); rows=c.execute('SELECT gc.id,gc.initial_paise,COALESCE(SUM(gl.amount_paise),0) spent FROM gift_cards gc LEFT JOIN gift_card_ledger gl ON gl.gift_card_id=gc.id GROUP BY gc.id').fetchall(); c.close(); return page('Gift cards','<p>Gift card balance is ledger-based; negative ledger entries represent spending.</p><ul>'+''.join(f"<li>Card #{r['id']}: initial {r['initial_paise']} paise, ledger net {r['spent']} paise</li>" for r in rows)+'</ul>')
