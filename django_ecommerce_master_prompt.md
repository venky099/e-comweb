# Django E-Commerce Platform — Master Build Prompt (for Claude Code)

You are a senior Django/Python full-stack developer. I need you to **BUILD a complete, fully functional, professional E-Commerce Website from scratch** using Django.

Do not just explain the project. Do not give me only architecture or sample code. **ACTUALLY CREATE THE COMPLETE PROJECT** — apps, models, views, templates, Django admin customization, APIs (optional layer), authentication, validation, error handling, and deployment configuration.

---

## TECH STACK

**Core language / framework:** Python + Django (latest stable LTS)

**Frontend:** Django Template Language (DTL) — server-rendered, no separate React/Vue app. Use:
- Bootstrap 5 (or Tailwind via `django-tailwind`) for styling
- HTMX + a touch of Alpine.js for interactive bits (cart updates, filters, wishlist toggles) without full page reloads
- Django's `{% static %}` and `{% block %}` template inheritance — base template + child templates
- Responsive design for mobile, tablet, desktop

**Backend:** Django (MVT pattern), Django ORM, class-based views where they reduce boilerplate, function-based views where clarity wins

**Admin Panel:** Django's **built-in admin** — this is the single biggest structural difference from a typical MERN/MEAN build. Do NOT build a separate custom admin frontend from scratch. Instead, **heavily customize** `ModelAdmin` classes: `list_display`, `list_filter`, `search_fields`, `inlines`, custom actions, and a custom admin dashboard view for analytics/reports.

**Database:** PostgreSQL via Django ORM + migrations

**Auth:** Django's built-in `auth` system with a **custom User model** (extending `AbstractUser` to add phone, etc.) — session-based auth for the storefront, `django-allauth` optional for social login

**Optional API layer:** Django REST Framework (DRF) for a future mobile app / headless frontend, with `djangorestframework-simplejwt` for JWT if needed

**Payments:** Razorpay or Stripe Python SDK, server-side verification only

**Other:** Git, GitHub, AWS-ready deployment (Gunicorn + Nginx, RDS for Postgres, S3 for static/media via `django-storages`)

Use clean, professional, scalable Django project architecture (multiple apps, not one monolithic app).

---

## 1. PROJECT PLANNING

Two user types — **Customer** and **Admin/Staff**.

**Customer** capabilities: register, login, browse products, search, filter, sort, view product details, select variants, add to cart, add to wishlist, apply coupons, checkout, pay, place orders, track orders, cancel eligible orders, request returns/refunds, write reviews, manage profile, manage addresses, view order history.

**Admin/Staff** capabilities (mostly delivered via **customized Django admin**, not a hand-built dashboard): view analytics dashboard, manage products/variants/inventory, manage categories, manage orders, manage customers, manage coupons/offers/banners, manage reviews, view sales/product/customer/revenue/inventory reports.

Note where Django admin covers this "for free": CRUD for products, categories, orders, coupons, banners, reviews is essentially `ModelAdmin` configuration. Reserve custom views/templates only for things Django admin genuinely can't do well (charts, aggregated reports).

---

## 2. DJANGO APP STRUCTURE

Split into focused apps under a single project:

```
ecommerce_project/
├── manage.py
├── requirements.txt
├── .env / .env.example
├── config/                  # project settings package
│   ├── settings/
│   │   ├── base.py
│   │   ├── dev.py
│   │   └── prod.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── accounts/            # custom User, profile, addresses, auth views
│   ├── catalog/             # products, categories, variants, brands
│   ├── inventory/           # stock tracking
│   ├── cart/                # cart + cart items (session + DB-backed)
│   ├── wishlist/
│   ├── orders/               # orders, order items, statuses, tracking
│   ├── payments/             # payment records, gateway integration, webhooks
│   ├── coupons/
│   ├── reviews/
│   ├── marketing/            # banners, offers, flash sales
│   ├── dashboard/            # custom admin-adjacent reports & charts
│   └── core/                 # shared utils, base templates, mixins
├── templates/
│   ├── base.html
│   └── ...
├── static/
└── media/
```

---

## 3. MODELS (map of the old "DATABASE" section)

Create proper relational models with `ForeignKey`/`OneToOneField`/`ManyToManyField`, `unique=True` / `null=False` constraints, `db_index=True` where it matters, and `created_at`/`updated_at` (`auto_now_add`, `auto_now`) on every model:

- `accounts`: `User` (custom, `AbstractUser` + phone), `Address`
- `catalog`: `Category` (self-referential for subcategories), `Brand`, `Product`, `ProductImage`, `ProductVariant` (size/color/etc.)
- `inventory`: stock fields on `ProductVariant` or a dedicated `Inventory` model — available / reserved / sold quantities
- `cart`: `Cart`, `CartItem`
- `wishlist`: `Wishlist`, `WishlistItem`
- `orders`: `Order`, `OrderItem`, status as a `TextChoices`/`IntegerChoices` enum
- `payments`: `Payment` (gateway, status, transaction id)
- `coupons`: `Coupon`, `CouponUsage`
- `reviews`: `Review` (with `verified_purchase` boolean, tied to a completed `OrderItem`)
- `marketing`: `Banner`, `Offer`, `FlashSale`
- Use Django migrations for all schema — never hand-edit the DB.

---

## 4. CUSTOMER-FACING TEMPLATES / PAGES

Build server-rendered pages (Django views + templates), each responsive:

**Home** — header (logo, search bar, category nav, login/register, account, wishlist, cart icon), hero banner, offers, featured products, new arrivals, best sellers, flash sales, category tiles, reviews carousel, footer (about, contact, policies, social, copyright).

**Product listing** — product cards (image, name, price, original price, discount %, rating, stock status, wishlist + add-to-cart buttons); filters (category, brand, price range, rating, size, color, availability) built as a Django form bound to querystring params; sorting (price asc/desc, newest, popularity, rating, discount); pagination via Django's `Paginator` — never load the full queryset unpaginated.

**Product detail** — image gallery, name, price/discount, description, specs, brand, SKU, stock, variant selectors (size/color), quantity selector, add-to-cart, buy-now, wishlist, ratings breakdown, reviews list, related/recommended products (simple queryset — same category / frequently bought together).

**Search** — search by name/brand/category using Django ORM `Q` objects or `django.contrib.postgres.search` (full-text search) for relevance; autocomplete via a small HTMX-powered endpoint if practical.

**Auth** — register (name, email, phone, password, confirm), login, logout, password reset — all via Django's auth views/forms, extended with the custom User model. Never store plaintext passwords (Django hashes by default — keep it that way, don't roll custom hashing).

**Customer dashboard** — profile, edit profile, change password, orders, order detail/tracking, wishlist, saved addresses, returns/refunds — all `login_required` views scoped to `request.user`.

**Cart** — item list (image, name, variant, qty, unit price, discount, subtotal), delivery charge, coupon discount, total; increase/decrease/remove; stock-aware (never allow qty beyond available stock); persist in DB for logged-in users, session for guests, merge session cart into DB cart on login.

**Address management** — add/edit/delete/set default; fields: name, phone, address, city, state, country, pincode.

**Checkout** — cart → address → shipping → coupon → payment → confirmation, as a multi-step flow (Django `FormWizard`-style or simple sequential views). Recompute every total server-side — never trust anything posted from the client.

---

## 5. DJANGO ADMIN CUSTOMIZATION (replaces the hand-built "Admin Panel" sections)

This is where most of the original spec's "Admin Panel / Product Management / Category Management / Inventory / Order Management / Customer Management / Coupons / Marketing" sections collapse into **`admin.py` customization**:

- Register every model with a tailored `ModelAdmin`: `list_display`, `list_filter`, `search_fields`, `list_editable` for quick stock/price edits, `readonly_fields` where needed
- `TabularInline`/`StackedInline` for `ProductImage` and `ProductVariant` inside the `Product` admin
- Custom admin **actions** (bulk mark featured/best-seller, bulk enable/disable, bulk stock update)
- Restrict/scope querysets per staff role via `get_queryset` overrides if you need partial admin access
- Protect the admin at `/admin/` behind `is_staff`/`is_superuser`; consider a non-default URL in production
- Only build custom (non-admin) views for things `ModelAdmin` can't express well: the analytics dashboard and CSV report exports (see §6)

---

## 6. DASHBOARD & REPORTS (custom `dashboard` app)

Since Django admin's default index page is just a model list, build one custom staff-only dashboard view:

- **Live stats** (computed from real querysets via `aggregate`/`annotate` — never hardcoded): total sales, today's sales, monthly revenue, total/today's orders, total customers, total products, pending/delivered/cancelled/returned orders, low-stock and out-of-stock products
- **Charts**: revenue, orders, customers, product sales, category performance — render with Chart.js, fed by a small JSON endpoint (plain Django view or DRF) that returns aggregated data
- **Reports**: sales (daily/weekly/monthly/yearly), product (best sellers, low sellers, out of stock), customer (new/active/top), revenue (gross, discounts, delivery, refunds, net), inventory (current/low/out/movement) — with CSV export via Django's `HttpResponse` + `csv` module

---

## 7. WISHLIST, CART, CHECKOUT, PAYMENTS, ORDERS, COUPONS, REVIEWS — business logic

Keep these functionally identical to a standard e-commerce build, but implemented as Django model methods / service functions, called from views (or DRF viewsets if you add the API layer):

- **Wishlist**: add/remove/view, move-to-cart, stock check — stored in Postgres, tied to `request.user`
- **Cart**: quantity changes clamp to available stock; totals (subtotal, delivery, coupon discount, grand total) computed server-side in a `Cart` model method, never in the template or JS
- **Coupons**: percentage/fixed discount, min order value, max discount cap, expiry, usage limit — validated **server-side only** on apply and again at order placement; never trust a discount value posted from the client
- **Payments**: architecture for UPI/card/net banking/wallet/COD; integrate Razorpay or Stripe server SDK; verify payment signature/webhook server-side; keep keys in environment variables, never in templates/JS; if gateway keys aren't available yet, build a safe mock/dev payment flow behind a settings flag so the rest of order flow is testable
- **Orders**: statuses as an enum (Pending → Confirmed → Processing → Shipped → Delivered / Cancelled / Returned / Refund Initiated / Refunded); generate a unique order ID (e.g. UUID or a formatted sequence); reduce stock on successful order placement inside a DB transaction (`select_for_update` to avoid race conditions), restore stock on eligible cancellation/return
- **Reviews**: star rating + text; only allow a review if the user has a `Delivered` `OrderItem` for that product (`verified_purchase=True`); show average rating + distribution

---

## 8. OPTIONAL REST API LAYER (Django REST Framework)

If you want the storefront to also be API-driven (for a future mobile app), add DRF viewsets/serializers mirroring the models above, with JWT auth via `simplejwt`. Keep this as an additive layer — the primary storefront stays server-rendered Django templates. Suggested endpoints, mirroring the original spec:

```
POST /api/auth/register/
POST /api/auth/login/
POST /api/auth/logout/

GET  /api/products/
GET  /api/products/<id>/
POST/PUT/DELETE /api/products/<id>/   (staff only)

GET/POST/PUT/DELETE /api/categories/
GET/POST/PUT/DELETE /api/cart/
GET/POST/DELETE      /api/wishlist/
GET/POST             /api/orders/
GET                  /api/orders/<id>/
PUT                  /api/orders/<id>/status/   (staff only)
GET                  /api/customers/
GET/POST/PUT/DELETE  /api/coupons/              (staff only)
```

---

## 9. SECURITY

Lean on what Django already gives you, and configure it correctly rather than reinventing it:
- CSRF protection (on by default — don't disable it on forms)
- Password hashing (Django's default PBKDF2, or switch to Argon2 via `django-argon2`)
- `django-environ` (or `python-decouple`) for environment variables — `SECRET_KEY`, `DATABASE_URL`, payment keys, never committed
- `django-ratelimit` on login/checkout endpoints
- Production security settings: `DEBUG=False`, explicit `ALLOWED_HOSTS`, `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`
- Role-based access via `@login_required`, `@staff_member_required`, `UserPassesTestMixin`, and DRF permission classes for the API layer
- Parameterized queries by default through the ORM (avoid raw SQL; if unavoidable, use parameterized `cursor.execute`)
- Never expose passwords, `SECRET_KEY`, DB credentials, or payment secrets in templates, JS, or logs

---

## 10. ERROR HANDLING

- Custom `404.html`, `500.html`, `403.html` templates
- Django's `messages` framework for toast-style notifications
- Form validation via Django `Form`/`ModelForm` with clear field errors rendered in templates
- Global exception handling for the DRF layer (`exception_handler` override) if the API is built
- Graceful empty states (empty cart, empty wishlist, no search results) in templates

---

## 11. RESPONSIVE UI

Every page — header, nav, product grid, product detail, cart, checkout, forms, admin, dashboard tables/charts — must work cleanly on mobile, tablet, laptop, desktop. Use Bootstrap's grid/utilities (or Tailwind) mobile-first; avoid fixed-width layouts.

---

## 12. ENVIRONMENT VARIABLES

`.env.example`:

```
DJANGO_SECRET_KEY=
DJANGO_DEBUG=False
DATABASE_URL=postgres://user:password@host:5432/dbname
ALLOWED_HOSTS=
PAYMENT_KEY=
PAYMENT_SECRET=
AWS_STORAGE_BUCKET_NAME=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

Never commit `.env`.

---

## 13. DATABASE SETUP

- Migrations for every app (`makemigrations` / `migrate`)
- A management command (`seed_data`) to populate sample categories, products, variants, and a dev admin account
- `createsuperuser` documented in the README for first admin access
- Project must run against a fresh Postgres database with one command sequence

---

## 14. TESTING

Use Django's `TestCase` (or `pytest-django`) to cover:
- Auth: registration, login, invalid login, protected views
- Catalog: create/read/update/delete products (as staff), permission checks
- Cart: add, update quantity, remove, stock clamping
- Orders: creation, stock reduction, status transitions
- Coupons: valid, invalid, expired, over usage-limit
- Authorization: customer blocked from admin/staff views and API endpoints; staff allowed

Fix everything the test suite surfaces before moving on.

---

## 15. GITHUB

- `README.md` covering: project overview, features, tech stack, folder structure, installation, Postgres setup, environment variables, database setup (migrate + seed + createsuperuser), running the dev server, API endpoints (if built), admin access, deployment
- `.gitignore` (Python/Django-specific: `*.pyc`, `__pycache__/`, `.env`, `db.sqlite3`, `media/`, `staticfiles/`, venv folders)
- `.env.example`

---

## 16. AWS DEPLOYMENT PREP

Prepare (don't require my credentials):
- Gunicorn as the WSGI server behind Nginx
- RDS for PostgreSQL
- S3 + `django-storages` for static and media files
- Environment variables via the hosting platform (Elastic Beanstalk / EC2 + systemd)
- Domain, SSL/HTTPS (Let's Encrypt or ACM), CORS if the API layer is exposed separately
- Security groups, database backups, `collectstatic` in the deploy pipeline, production settings module

---

## 17. PERFORMANCE

- `select_related`/`prefetch_related` to avoid N+1 queries on listing/detail pages
- Django's `Paginator` everywhere lists appear
- DB indexes on frequently filtered/sorted fields (price, category, created_at)
- Caching (Django's cache framework, Redis backend) for category lists, homepage banners, popular products
- Image handling via Pillow, `django-imagekit`/`sorl-thumbnail` for thumbnails
- Avoid unnecessary queries in templates (no queries inside loops)

---

## 18. BUSINESS RULES (server-side only, no exceptions)

Every calculation — product price, quantity, stock, coupon validity/discount, delivery charge, order total, payment status, order status transitions, user permissions — must be verified/computed in the backend (model methods, view logic, or DRF serializers). Never trust a total, discount, or status posted from the client.

---

## 19. HOW I WANT YOU TO BUILD IT

Do not try to deliver the entire application in one response if that risks incomplete/truncated code. Build it **STEP BY STEP**:

- **Phase 1** — Project scaffold, settings split (base/dev/prod), all app skeletons, models, migrations
- **Phase 2** — Django admin customization (ModelAdmin classes, inlines, actions) + custom User model + auth
- **Phase 3** — Customer-facing templates: home, listing, detail, search, account pages
- **Phase 4** — Cart, wishlist, address management, checkout flow
- **Phase 5** — Payments integration, order placement/tracking, coupons
- **Phase 6** — Reviews, marketing (banners/offers), custom dashboard + reports
- **Phase 7** — Testing, security hardening, error pages, performance passes
- **Phase 8** — README, `.gitignore`, `.env.example`, AWS deployment prep

For every phase: tell me what you're implementing, give the exact file path, give complete code (no incomplete snippets), tell me where to create each file, give install/run/test commands, and make sure new code is compatible with everything built in earlier phases.

If a response gets too long, **STOP at a logical point** and wait for me to say `CONTINUE`. When I say `CONTINUE`, resume exactly where you stopped — don't restart the project or change the architecture unnecessarily.

---

## FINAL REQUIREMENT

The finished result must be a **real, full-stack Django e-commerce application** with: customer storefront + Django-admin-powered admin panel + PostgreSQL + (optional) REST API + authentication/authorization + products/categories/variants + search/filters/sorting + cart/wishlist/checkout/payments + order tracking + inventory + coupons + reviews + returns/refunds + customer management + marketing + reports/analytics + responsive UI + security + error handling + GitHub-ready + AWS deployment prep.

No core functionality left as a TODO or a fake button.

**START WITH PHASE 1 NOW.**
