-- Safe schema additions for release scheduling and flash sales.
-- app.py also applies these with guarded ALTER TABLE checks at startup.
ALTER TABLE books ADD COLUMN release_date TEXT;
ALTER TABLE books ADD COLUMN sale_price_paise INTEGER;
ALTER TABLE books ADD COLUMN sale_start TEXT;
ALTER TABLE books ADD COLUMN sale_end TEXT;
