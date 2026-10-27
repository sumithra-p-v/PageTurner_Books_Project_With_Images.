# Part 6 implementation notes

Applied the PDF's steps 121–140 while retaining the supplied project files and existing routes/features.

- 121–123: escaped logged-in welcome/guest line and server-generated footer year in base template and singular/plural book count on the home page.
- 124: shared `inr` Jinja filter now formats paise as `Rs 1,250.00`.
- 125: retained existing custom 404 template and registered app handler (existing handler preserved).
- 128: cart free-shipping hint uses a single ₹500 threshold constant and integer paise arithmetic.
- 129: login/logout flash messages and dismissible category-styled flash alerts.
- 130: server-side empty-cart checkout redirect with flash.
- 131–137: hover transitions, responsive 4/2/1 book grid, sticky shared header, reusable button styles, active navigation, smooth back-to-top link.
- 138: Place Order button disables and shows Please wait on submit.
- 139: show/hide password on login/register.
- 140: missing/broken book cover fallback with stable placeholder area.
- Home cards truncate long descriptions to 100 characters.

Note: the provided project stores book prices using its existing convention; this update formats the existing paise helper as requested but does not migrate or rewrite stored prices. Back up `database.db` before future schema changes.


## User-requested final verification (2026-10-27)
- The homepage now has a larger hero banner and a prominent **Explore Features** section, including Loyalty Points.
- Loyalty rule: 1 point per ₹100 spent; signed-in customers see their balance, guests are prompted to sign in.
- Feature 122: server-generated year is used in the shared base footer; homepage also displays the year because the storefront route renders its own template.
- Feature 123: filtered book count displays “Showing 1 book”, “Showing N books”, or “No books yet”.
- Feature 126: book detail uses `.stock-status.in-stock` / `.stock-status.out-of-stock`; colour rules are in `static/style.css`.
- Feature 127: homepage book cards display at most 100 description characters and add `...` only when longer; book details retain the full description.
- Feature 128: free-shipping threshold is `FREE_SHIPPING_LIMIT_PAISE = 50000` and cart totals are calculated in integer paise.
- Feature 139: login and registration have a Show checkbox that toggles the password input while retaining the same field name.
