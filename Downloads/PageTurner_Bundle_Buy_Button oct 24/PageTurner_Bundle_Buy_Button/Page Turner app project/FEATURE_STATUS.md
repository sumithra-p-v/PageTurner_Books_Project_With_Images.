# PageTurner feature status

This file distinguishes features that are implemented from items that still need end-to-end integration and tests. The add-on PDF is a specification, not evidence that every feature has been completed.

## Existing features retained
- Flask bookstore, catalogue, cart, checkout, order history, coupons, wishlist, customer registration/login, admin tools, inventory and existing database.
- Customer lock/unlock: five failed password attempts trigger a 15-minute temporary lock; admin can permanently lock/unlock customers at `/admin/customers`.
- CSRF checks on routes that use the existing `csrf_required` decorator.

## Add-on features implemented in this update
- Step 42: session-based recently viewed list (last five; deleted books are skipped) and `/recently-viewed`.
- Step 43: author page at `/author/<name>` with a single book query and 404 for unknown authors.
- Step 45: two-book comparison at `/compare`; invalid counts produce a clear response.
- Step 50: helpful review votes with a database UNIQUE pair at `/review/<id>/helpful`.
- Step 51: report review and admin hide/keep workflow; hidden flag is stored. Existing public review rendering should be checked for hidden filtering before production use.
- Step 52: book requests deduplicated per user/title/author and admin list at `/admin/book-requests`.
- Step 53: random token wishlist share and disable link at `/wishlist/share`, public read-only page `/w/<token>`.
- Step 54: price history for changes made through Advanced admin tools.
- Step 55: bulk price increase in one transaction, paise integer arithmetic.
- Step 56: CSS-only 7-day sales chart at `/admin/sales-chart`.
- Step 58: customer data JSON export at `/account/export`.
- Step 59: password-confirmed account anonymisation at `/account/delete` (email is replaced with a non-deliverable unique placeholder because the legacy schema requires email NOT NULL; orders are retained).
- Step 61: checkout token table and token issuance endpoint `/checkout/token`. Full token consumption still needs to be wired into the existing checkout transaction before treating checkout as idempotent.
- Step 66: optimistic-lock edit endpoint `/book/<id>/optimistic-edit`, with version check and conflict response.
- Step 67: keyset pagination on `/api/books?after=<id>&limit=10`, capped at 50.
- Step 68: audit-chain verification page and storage table. All administrative mutations still need to be routed through a single hash-chain writer for full coverage.
- Step 69: notification outbox table and admin processing endpoint `/admin/outbox/run`; order events still need to enqueue rows in their originating transactions.
- Step 71: TOTP secret setup and code verification at `/account/totp/setup`; the login route still needs a second-factor challenge before this is full two-factor authentication.
- Step 74: monthly growth report at `/admin/reports/growth` using SQLite window functions.
- Admin tools at `/admin/advanced`: bundle creation, release dates, price history, bulk increase and pricing-rule records.

## Still incomplete / must not be represented as finished
- Step 44: release-date ribbons and server-side refusal of future-book orders.
- Step 46: bundles are defined but bundle-cart checkout, atomic stock decrement and bundle invoice lines are not wired.
- Step 47: flash-sale pricing must be applied consistently in cart, order and invoice.
- Step 48: gift wrap and escaped gift message at checkout/invoice.
- Step 49: loyalty points earning/redemption and reversal on cancellations/returns.
- Step 51: hidden reviews must be excluded from customer-facing review lists and rating calculations.
- Step 54: price-history writes for every possible price mutation need centralisation.
- Step 57: staff role restrictions across all admin endpoints.
- Step 60: dark-mode toggle and no-flash persistence.
- Step 61: consume checkout token in the same transaction as order creation and return existing order on retry.
- Step 62: edit order lines and recalculate coupon/shipping/tax/total atomically.
- Step 63: apply pricing rules to cart, order and invoice in priority order.
- Step 64: gift card creation/redemption, expiry messaging and concurrency-safe ledger spending.
- Step 65: customer backorder placement and automatic allocation/fulfilment queue integration.
- Step 68: write every admin change to the hash chain from one helper.
- Step 69: enqueue outbox events atomically with source events and add robust retry/failure controls.
- Step 70: subscription creation, stock/address validation, actual due order creation and skip reasons.
- Step 71: enforce TOTP on login and test replay refusal in the complete authentication flow.
- Step 72: scoped API keys need read_orders, revocation UI and per-key rate limiting.
- Step 73: permissions must replace existing role checks across every route and be editable from the UI.
- Step 75: personalised co-purchase recommendations need the one-query scoring implementation and fallback.
- Steps 76–78: automated test suite with at least 20 tests, concurrency stress script and fresh-database defence run.

## Running
Open the inner `Page Turner app project` directory in VS Code, then run `python -m pip install -r requirements.txt` and `python app.py`.

## Safety
Back up `database.db` before applying schema changes. New tables/columns are created by `init_advanced_db()` when the application starts. Test with a copy of the database before using real customer data.


## Delivery note (2026-10-24)
This ZIP consolidates the current working project and includes the original add-on specification at
`docs/bookstore_addon_notes_part5.pdf`. It is **not yet a complete, end-to-end verified implementation of all Steps 42–78**.
Do not treat any feature marked partial or incomplete above as complete. Checkout idempotency, bundles, flash sales,
gift wrap, loyalty, order editing, pricing rules, gift-card redemption, backorders, complete staff/permission enforcement,
subscriptions, enforced two-factor login, scoped API keys, recommendations, and the required test/stress scripts still
need full integration and verification.
