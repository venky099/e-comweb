# Lumen Store — Django E-Commerce Platform

A complete, production-shaped e-commerce application: a server-rendered customer
storefront, a heavily customised Django admin for operations, a staff analytics
dashboard, and a full REST API — all backed by PostgreSQL.

Every price, discount, stock level, coupon and order total is computed on the
server. The browser can choose *what* to buy; it can never tell the server *what
it costs*.

---

## Contents

- [Features](#features)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Quick start](#quick-start)
- [Dependencies and Python version](#dependencies-and-python-version)
- [PostgreSQL setup](#postgresql-setup)
- [Environment variables](#environment-variables)
- [Database setup, seeding and admin access](#database-setup-seeding-and-admin-access)
- [Running the app](#running-the-app)
- [REST API](#rest-api)
- [Admin & staff dashboard](#admin--staff-dashboard)
- [Payments](#payments)
- [Business rules](#business-rules)
- [Testing](#testing)
- [Security](#security)
- [Performance](#performance)
- [AWS deployment](#aws-deployment)
- [Troubleshooting](#troubleshooting)

---

## Features

### Customer storefront
- **Home** — hero carousel, category tiles, live flash sale with countdown, offers
  and coupon strip, featured / new arrival / best-seller rails, review carousel
- **Catalog** — filterable, sortable, paginated product grid; sidebar facets
  (category, brand, price, rating, size, colour, availability) driven by a single
  Django form bound to the querystring
- **Search** — PostgreSQL full-text search with ranking (falls back to `Q`-object
  matching on other engines), plus an HTMX autocomplete dropdown
- **Product detail** — image gallery, live variant selector (size/colour), server-
  verified stock and pricing, spec sheet, rating histogram, reviews, related products
- **Cart** — DB-backed for users, session-backed for guests, merged on login;
  quantities clamped to real stock; totals computed by model methods
- **Wishlist** — toggle from any product card, move one or all items to the cart
- **Checkout** — address → payment → confirmation, with every total recomputed
  server-side at placement
- **Orders** — history, search/filter, detail, tracking timeline, cancellation,
  returns, "buy again"
- **Account** — profile, avatar, password change, address book with defaults
- **Reviews** — verified-purchase only, star histogram, photos, helpful votes
- **Auth** — register, login (email *or* username), logout, password reset by email

### Staff & operations
- **Django admin** — every model registered with tailored `list_display`,
  `list_filter`, `search_fields`, `list_editable`, inlines and bulk actions
- **Analytics dashboard** (`/staff/`) — live KPIs, Chart.js revenue/orders/
  customers/category/payment charts fed by JSON endpoints
- **Reports** (`/staff/reports/`) — sales (daily/weekly/monthly), products
  (best sellers, slow movers, low/out of stock), customers (top, new, inactive),
  revenue breakdown, inventory levels and movement — all CSV-exportable
- **Inventory** — reserve → commit → release/restore lifecycle with a full,
  append-only stock movement audit log
- **Order desk** — bulk status transitions that respect the transition rules,
  return approvals that restore stock, refund recording

### REST API
A complete DRF layer with JWT auth, mirroring the storefront's business rules —
see [REST API](#rest-api).

---

## Tech stack

| Layer | Choice |
|---|---|
| Language / framework | Python 3.11-3.13, Django 5.2 LTS |
| Database | PostgreSQL (via `DATABASE_URL`) |
| Frontend | Django Template Language, Bootstrap 5, HTMX, Alpine.js, Chart.js |
| API | Django REST Framework + SimpleJWT + drf-spectacular (OpenAPI) |
| Auth | Django `auth` with a custom `User` model (email or username login) |
| Payments | Razorpay / Stripe server SDKs, plus a signed mock gateway for dev |
| Config | `django-environ` (12-factor, `.env`) |
| Caching | Redis (optional) with an in-memory fallback |
| Static / media | WhiteNoise, or S3 via `django-storages` |
| Deployment | Gunicorn + Nginx + systemd |

---

## Project structure

```
e-com/
├── manage.py                  # selects test settings automatically for `test`
├── requirements.txt           # version ranges for development
├── requirements.lock.txt      # exact versions for reproducible installs
├── .env.example               # copy to .env
├── config/
│   ├── settings/
│   │   ├── base.py            # shared settings
│   │   ├── db.py              # dev/test database resolution + SQLite fallback
│   │   ├── dev.py             # DEBUG, browsable API, auto SQLite fallback
│   │   ├── test.py            # fast hashing, no throttling, isolated media
│   │   └── prod.py            # HTTPS, HSTS, secure cookies, S3
│   ├── urls.py                # root URLconf + error handlers
│   ├── wsgi.py / asgi.py      # default to production settings
├── apps/
│   ├── core/                  # abstract models, admin mixins, error pages,
│   │                          # seed_data command, placeholder image factory
│   ├── accounts/              # custom User, Address, auth views & forms
│   ├── catalog/               # Category, Brand, Product, Image, Variant,
│   │                          # search, filters, listing & detail views
│   ├── inventory/             # Inventory, StockMovement, stock service layer
│   ├── cart/                  # Cart, CartItem, cart service layer, merge middleware
│   ├── wishlist/              # Wishlist, WishlistItem
│   ├── orders/                # Order, OrderItem, history, returns, checkout,
│   │                          # order service layer (the business core)
│   ├── payments/              # Payment, Refund, WebhookEvent, gateway adapters
│   ├── coupons/               # Coupon, CouponUsage, validation service
│   ├── reviews/               # Review, images, helpful votes, eligibility rules
│   ├── marketing/             # Banner, Offer, FlashSale
│   ├── dashboard/             # staff analytics, reports, CSV exports
│   └── api/                   # DRF serializers, viewsets, permissions, schema
├── templates/                 # base + per-app templates and HTMX partials
├── static/css|js/             # storefront styles and behaviour
├── deploy/                    # gunicorn, nginx, systemd, deploy.sh
└── media/                     # user uploads (gitignored)
```

Each app owns its models, services, views, admin and tests. **Business logic
lives in `services.py`**, not in views — so the storefront and the API cannot
drift apart.

---

## Quick start

```bash
# 1. Clone and enter the project
git clone <your-repo-url> e-com && cd e-com

# 2. Create a virtualenv
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt          # version ranges, resolves to current releases
# pip install -r requirements.lock.txt   # or: exact versions, for a reproducible build

# 4. Configure (works as-is in development: falls back to SQLite
#    when no PostgreSQL server answers)
cp .env.example .env

# 5. Create the schema
python manage.py migrate

# 6. Load a demo catalog (optional but recommended)
python manage.py seed_data

# 7. Run
python manage.py runserver
```

Open <http://127.0.0.1:8000/>.

Generate a real secret key with:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## Dependencies and Python version

**Supported Python: 3.11 - 3.13.** Django 5.2 LTS does not support Python 3.14;
on 3.14 several dependencies have no prebuilt wheels and pip will try (and
usually fail) to compile them from C source.

Check what you have, and create the venv against a supported version explicitly:

```bash
py --list                  # Windows: lists installed versions
py -3.12 -m venv venv      # Windows
python3.12 -m venv venv    # macOS / Linux
```

Two dependency files, for two different jobs:

| File | Contents | Use it when |
|---|---|---|
| `requirements.txt` | Version **ranges** (`Django>=5.2,<6.0`) | Normal development. Resolves to current releases, so wheels exist for your Python. |
| `requirements.lock.txt` | **Exact** versions from `pip freeze` | CI, production, or reproducing a known-good environment exactly. |

After changing `requirements.txt`, refresh the lockfile:

```bash
pip install -r requirements.txt --upgrade
pip freeze | sort > requirements.lock.txt
python manage.py test        # confirm the new versions still pass
```

`psycopg2-binary` is only needed for PostgreSQL. If it fails to install and you
are running locally on SQLite (`USE_SQLITE=True`), comment it out of
`requirements.txt` — nothing else depends on it.

---

## PostgreSQL setup

PostgreSQL is the target database.

```bash
# macOS
brew install postgresql@16 && brew services start postgresql@16

# Debian / Ubuntu
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql

# Windows: install from https://www.postgresql.org/download/windows/
```

Create the database and role:

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE ecommerce;
CREATE USER ecommerce_user WITH PASSWORD 'choose-a-strong-password';

ALTER ROLE ecommerce_user SET client_encoding TO 'utf8';
ALTER ROLE ecommerce_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE ecommerce_user SET timezone TO 'Asia/Kolkata';

GRANT ALL PRIVILEGES ON DATABASE ecommerce TO ecommerce_user;
\c ecommerce
GRANT ALL ON SCHEMA public TO ecommerce_user;

-- Optional: enables trigram similarity in product search ranking.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
\q
```

Then set in `.env`:

```
DATABASE_URL=postgres://ecommerce_user:choose-a-strong-password@localhost:5432/ecommerce
USE_SQLITE=False
```

> **Running without PostgreSQL.** You do not have to do anything. If no server
> answers at `DATABASE_URL`, development and test settings fall back to a local
> SQLite file and print a line explaining why — so a fresh clone runs with no
> database setup at all. The ORM layer is engine-agnostic, with one deliberate
> degradation: full-text search drops to `icontains` matching with a hand-rolled
> relevance ranking (see `apps/catalog/search.py`).
>
> | Setting | Effect |
> |---|---|
> | *(nothing)* | Probe `DATABASE_URL`; use SQLite if nothing answers |
> | `USE_SQLITE=True` | Always SQLite, skip the probe |
> | `DB_FALLBACK=False` | Require PostgreSQL; a missing server is an error |
>
> The probe only opens a TCP connection, so it detects an *absent* server, not a
> misconfigured one — wrong credentials still raise, as they should.
> **Production never falls back:** `config/settings/prod.py` has no SQLite path.

---

## Environment variables

Copy `.env.example` to `.env`. `.env` is gitignored and must never be committed.

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Cryptographic signing key. **Required in production.** | dev-only fallback |
| `DJANGO_DEBUG` | Debug mode | `False` |
| `ALLOWED_HOSTS` | Comma-separated hostnames | empty |
| `DATABASE_URL` | PostgreSQL connection URL | localhost default |
| `USE_SQLITE` | Force SQLite, skipping the reachability probe | `False` |
| `DB_FALLBACK` | Allow the dev/test SQLite fallback when no server answers | `True` |
| `REDIS_URL` | Cache backend; blank uses in-memory | blank |
| `PAYMENT_GATEWAY` | `razorpay`, `stripe` or `mock` | `mock` |
| `PAYMENT_KEY` / `PAYMENT_SECRET` | Gateway credentials | blank |
| `PAYMENT_WEBHOOK_SECRET` | Webhook signature secret | blank |
| `SITE_NAME`, `SITE_TAGLINE` | Storefront branding | Lumen Store |
| `SUPPORT_EMAIL`, `SUPPORT_PHONE` | Shown in header/footer | test values |
| `CURRENCY_SYMBOL`, `DEFAULT_CURRENCY` | Money display | `₹`, `INR` |
| `DELIVERY_CHARGE` | Flat delivery fee | `49.00` |
| `FREE_DELIVERY_THRESHOLD` | Free delivery above this | `999.00` |
| `TAX_RATE_PERCENT` | Applied to the discounted subtotal | `0.00` |
| `MAX_CART_QUANTITY_PER_ITEM` | Per-line cap | `10` |
| `ORDER_CANCEL_WINDOW_HOURS` | Free cancellation window | `24` |
| `RETURN_WINDOW_DAYS` | Return window after delivery | `7` |
| `LOW_STOCK_THRESHOLD` | Low-stock alert level | `5` |
| `ADMIN_URL` | Non-default admin path in production | `staff-console/` |
| `USE_S3` + `AWS_*` | S3 static/media storage | `False` |
| `EMAIL_*` | SMTP settings | console backend |

Changing a business constant changes it everywhere — templates, service layer
and API all read from settings.

---

## Database setup, seeding and admin access

```bash
# Apply all migrations
python manage.py migrate

# Create your own admin account
python manage.py createsuperuser

# Or load a full demo store (catalog, customers, orders, reviews, reports data)
python manage.py seed_data

# Wipe demo data and reload
python manage.py seed_data --flush

# Options
python manage.py seed_data --orders 200 --customers 50   # bigger dataset
python manage.py seed_data --no-images                   # skip image generation (faster)
```

`seed_data` creates:

- 19 categories (5 roots with subcategories), 10 brands, 35 products
- Size/colour variants for clothing and footwear, stock levels including
  deliberate low-stock and out-of-stock cases
- Generated placeholder imagery for products, categories, brands and banners
- 5 coupons covering percentage, fixed, free-shipping, category-restricted and
  expired cases
- Banners, offers and a live flash sale
- 14 customers with addresses, 45 orders spread over 120 days across every
  status, and verified reviews

Demo credentials printed by the command:

| Role | Email | Password |
|---|---|---|
| Admin | `admin@lumenstore.test` | `admin12345` |
| Customer | `aarav.sharma0@example.com` | `customer12345` |

> These are development credentials for seeded data only. Change or remove them
> before exposing an instance.

---

## Running the app

```bash
python manage.py runserver
```

| Surface | URL |
|---|---|
| Storefront | <http://127.0.0.1:8000/> |
| Product catalog | `/products/` |
| Cart | `/cart/` |
| Customer account | `/accounts/` |
| Staff dashboard | `/staff/` |
| Reports | `/staff/reports/` |
| Django admin | `/admin/` |
| API docs (Swagger) | `/api/docs/` |
| API docs (ReDoc) | `/api/redoc/` |
| OpenAPI schema | `/api/schema/` |

---

## REST API

An additive layer over the same service functions the storefront uses — so
stock rules, coupon validation and order transitions behave identically.

- **Base**: `/api/v1/` (the unversioned `/api/` aliases are also served)
- **Auth**: JWT (`Authorization: Bearer <token>`), session auth for the browsable API
- **Docs**: Swagger UI at `/api/docs/`, OpenAPI 3 schema at `/api/schema/`
- **Errors**: one envelope for everything

```json
{"error": {"type": "validation_error",
           "message": "Enter a valid email address.",
           "detail": {"email": ["Enter a valid email address."]},
           "status_code": 400}}
```

### Authentication

| Method | Endpoint | Notes |
|---|---|---|
| POST | `/api/v1/auth/register/` | Creates the account, returns access + refresh |
| POST | `/api/v1/auth/login/` | Accepts email **or** username |
| POST | `/api/v1/auth/logout/` | Blacklists the refresh token |
| POST | `/api/v1/auth/refresh/` | New access token (rotating refresh) |
| POST | `/api/v1/auth/verify/` | Token validity check |
| GET/PATCH | `/api/v1/auth/me/` | Current user profile |
| POST | `/api/v1/auth/change-password/` | Requires the old password |

### Catalog (public read, staff write)

| Method | Endpoint |
|---|---|
| GET | `/api/v1/products/` — `?q=&category=&brand=&min_price=&max_price=&rating=&size=&color=&availability=&sort=&page=&page_size=` |
| GET | `/api/v1/products/{slug}/` |
| GET | `/api/v1/products/{slug}/variants/` · `/reviews/` |
| GET | `/api/v1/products/featured/` · `/best-sellers/` |
| POST / PUT / PATCH / DELETE | `/api/v1/products/{slug}/` *(staff only)* |
| GET | `/api/v1/categories/` · `/api/v1/categories/{slug}/products/` |
| GET | `/api/v1/brands/` |

### Cart, wishlist, orders *(authenticated)*

| Method | Endpoint |
|---|---|
| GET | `/api/v1/cart/` |
| POST | `/api/v1/cart/items/` — `{"variant_id": 1, "quantity": 2}` |
| PUT/PATCH | `/api/v1/cart/items/{id}/` |
| DELETE | `/api/v1/cart/items/{id}/remove/` · `/api/v1/cart/clear/` |
| POST | `/api/v1/cart/apply-coupon/` — `{"code": "SAVE500"}` |
| DELETE | `/api/v1/cart/remove-coupon/` |
| GET/POST/DELETE | `/api/v1/wishlist/` · `/api/v1/wishlist/{id}/move-to-cart/` |
| GET | `/api/v1/orders/` · `/api/v1/orders/{order_number}/` |
| POST | `/api/v1/orders/` — `{"address_id": 1, "payment_method": "cod"}` |
| POST | `/api/v1/orders/{order_number}/cancel/` · `/reorder/` |
| PUT | `/api/v1/orders/{order_number}/status/` *(staff only)* |
| GET/POST | `/api/v1/returns/` |
| GET/POST/PATCH/DELETE | `/api/v1/reviews/` · `/api/v1/reviews/{id}/helpful/` |
| GET/POST/PUT/DELETE | `/api/v1/addresses/` · `/api/v1/addresses/{id}/set-default/` |

### Staff

| Method | Endpoint |
|---|---|
| GET | `/api/v1/customers/` · `/api/v1/customers/{id}/orders/` |
| GET/POST/PUT/DELETE | `/api/v1/coupons/` |
| GET | `/api/v1/coupons/public/` *(public — safe field subset)* |
| GET | `/api/v1/dashboard/stats/` · `/api/v1/dashboard/charts/{chart}/` |

### Example session

```bash
# Register
curl -X POST http://127.0.0.1:8000/api/v1/auth/register/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"jane@example.com","first_name":"Jane",
       "password":"StrongPass!2345","password_confirm":"StrongPass!2345"}'

# Log in
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"jane@example.com","password":"StrongPass!2345"}' | jq -r .access)

# Browse and add to cart
curl "http://127.0.0.1:8000/api/v1/products/?sort=price_asc&availability=in_stock"
curl -X POST http://127.0.0.1:8000/api/v1/cart/items/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"variant_id": 1, "quantity": 2}'

# Place the order
curl -X POST http://127.0.0.1:8000/api/v1/orders/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"address_id": 1, "payment_method": "cod"}'
```

---

## Admin & staff dashboard

The Django admin **is** the admin panel — no parallel CRUD UI was built. Each
`ModelAdmin` is configured for real operational use:

- **Products** — inline images and variants (with live stock), `list_editable`
  price/status/flags for bulk merchandising, actions for feature/publish/archive
- **Orders** — line items, payments, returns and status history as inlines; bulk
  status actions routed through `services.transition_order`, so admin changes obey
  exactly the same transition rules and audit logging as the storefront
- **Inventory** — stock-level filter, two-step bulk restock action, read-only
  movement log
- **Coupons** — live/scheduled/expired/exhausted filter, redemption inline,
  total discount given
- **Customers** — annotated order count and lifetime value, address inline
- **Reviews** — moderation queue that refreshes product rating averages
- **CSV export** on every list via a shared admin mixin

What the admin genuinely cannot express well — charts and aggregated reports —
lives in the custom dashboard at `/staff/`, linked from the admin index.

---

## Payments

Three interchangeable gateways behind one interface (`apps/payments/gateways.py`):

| Gateway | Setting | Verification |
|---|---|---|
| Razorpay | `PAYMENT_GATEWAY=razorpay` | HMAC-SHA256 over `order_id\|payment_id` |
| Stripe | `PAYMENT_GATEWAY=stripe` | PaymentIntent re-fetched from Stripe |
| Mock | `PAYMENT_GATEWAY=mock` | HMAC signed with `SECRET_KEY` |

**The mock gateway is not a fake button.** It signs its own callback and the
server re-verifies that signature, so the development flow exercises the same
verification path production does — a tampered or unsigned callback is rejected
and the order stays unpaid.

A payment is only ever marked successful *after* server-side verification.
Webhooks are stored before processing and keyed by a unique event id, so a
gateway retry cannot double-apply.

If `PAYMENT_GATEWAY` names a real gateway but credentials are missing:
- with `DEBUG=True` it falls back to the mock and logs a warning;
- with `DEBUG=False` it **raises** — a misconfigured production deploy fails
  loudly rather than quietly accepting fake payments.

Webhook endpoint: `POST /payments/webhook/<gateway>/`

---

## Business rules

All enforced server-side, in the service layer:

**Stock lifecycle**

```
place_order()   reserve   (reserved +1)          order awaits payment
mark_paid()     commit    (reserved -1, available -1, sold +1)
payment failed  release   (reserved -1)
cancel/return   restore   (available +1, sold -1)
```

Every transition locks the inventory row with `select_for_update()` inside a
transaction, which is what stops two shoppers buying the same last unit. Every
change is written to an append-only `StockMovement` log.

**Money** — cart and order totals come from model methods. The coupon discount is
*recomputed on every read*: a coupon that expires while a cart sits idle silently
contributes nothing, and is re-validated again at order placement. Delivery is
free above the threshold, measured on the post-discount subtotal.

**Orders** — statuses form an explicit transition map (`Order.TRANSITIONS`);
illegal moves raise rather than silently applying. Cancellation is allowed while
unshipped and inside the configured window; staff can override. Returns require a
delivered order inside the return window, and completing one puts stock back.

**Reviews** — only a customer with a delivered order line for that product may
review it, once. `verified_purchase` is derived, never accepted from a client.

---

## Testing

```bash
python manage.py test                    # whole suite
python manage.py test apps.orders        # one app
python manage.py test apps.orders.tests.test_orders.StockLifecycleTests -v 2
```

`manage.py` selects `config.settings.test` automatically for the `test` command:
fast hashing, throttling and rate limiting disabled, isolated media directory,
mock payment gateway.

**288 tests** covering:

| Area | Examples |
|---|---|
| Auth | registration, duplicate email, weak passwords, email/username login, inactive accounts, protected views |
| Authorization | customers blocked from admin, staff dashboard, staff API endpoints and other users' records |
| Catalog | filtering, sorting, search, pagination, draft visibility, discount maths, staff CRUD |
| Cart | add, clamp to stock, per-item cap, update, remove, totals, guest→user merge |
| Coupons | valid, expired, inactive, exhausted, minimum order, per-user limit, case-insensitivity |
| Orders | placement, snapshots, reservation, oversell prevention, transitions, cancellation windows, returns, refunds |
| Inventory | reserve/commit/release/restore, backorders, movement log, availability checks |
| Payments | signature verification, forged/missing signature rejection, webhook replay, gateway misconfiguration |
| Reviews | eligibility, one-per-product, rating aggregation, helpful votes, API parity |
| Dashboard | access control, aggregate correctness, chart endpoints, CSV exports |
| API | JWT lifecycle, permissions, cart/checkout over JSON, error envelope, schema generation |

Several tests specifically assert that **posted totals are ignored** — that a
tampered client cannot dictate an order's price.

---

## Security

- **CSRF** protection on every form (only the gateway webhook is exempt, and it
  authenticates by signature instead)
- **Password hashing** via Django's PBKDF2; validators enforced identically in
  the storefront form and the API serializer
- **Rate limiting** (`django-ratelimit`) on login, registration and checkout;
  DRF throttling on the API
- **Ownership scoping** — every customer-facing queryset is filtered by
  `request.user`; another user's id in a URL returns 404, not their data
- **Role-based access** — `@login_required`, `@staff_member_required`,
  `LoginRequiredMixin`, and DRF permission classes (`IsStaff`, `IsOwner`,
  `IsStaffOrReadOnly`)
- **Secrets** from the environment via `django-environ`; `.env` is gitignored,
  and no key, hash or credential is ever rendered into a template or JS
- **Production settings** — `DEBUG=False`, explicit `ALLOWED_HOSTS`,
  `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`,
  1-year HSTS with preload, `X-Frame-Options: DENY`, nosniff, referrer policy,
  and a non-default admin URL
- **ORM everywhere** — parameterised queries by default, no raw SQL
- Run `python manage.py check --deploy` before shipping

---

## Performance

- `select_related` / `prefetch_related` on every listing and detail queryset
  (`Product.objects.with_related()` is the standard entry point)
- Database indexes on price, category, status, rating, sold count, order dates
  and every foreign key that is filtered or sorted
- `Paginator` on all list pages; DRF pagination on all list endpoints
- Denormalised counters (`rating_average`, `rating_count`, `sold_count`)
  maintained by signals so grids never aggregate per row
- Cached category navigation and homepage tiles, invalidated by signal on save
- Redis cache backend when `REDIS_URL` is set, in-memory otherwise
- WhiteNoise compressed, hash-named static files
- No queries inside template loops

---

## AWS deployment

Files in `deploy/`: `gunicorn.conf.py`, `nginx.conf`, `ecommerce.service`,
`ecommerce.socket`, `deploy.sh`.

### 1. RDS (PostgreSQL)

Create a PostgreSQL instance, keep it in a private subnet, and allow inbound
5432 **only** from the application security group. Then:

```
DATABASE_URL=postgres://user:password@your-db.xxxx.rds.amazonaws.com:5432/ecommerce
DB_SSLMODE=require
```

Enable automated backups (7–35 days) and a maintenance window.

### 2. S3 (static & media)

```
USE_S3=True
AWS_STORAGE_BUCKET_NAME=your-bucket
AWS_S3_REGION_NAME=ap-south-1
```

Prefer an **IAM role on the instance** over access keys. Block public ACLs and
serve through CloudFront. With `USE_S3=False` the app serves static files itself
through WhiteNoise, which is fine for a single node.

### 3. EC2 host

```bash
sudo apt update && sudo apt install -y python3-venv nginx postgresql-client
sudo mkdir -p /srv/ecommerce && sudo chown $USER /srv/ecommerce
git clone <repo> /srv/ecommerce && cd /srv/ecommerce
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt

cp .env.example .env      # fill in production values
export DJANGO_SETTINGS_MODULE=config.settings.prod

./venv/bin/python manage.py check --deploy
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
./venv/bin/python manage.py createsuperuser

sudo cp deploy/ecommerce.socket deploy/ecommerce.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ecommerce.socket ecommerce

sudo cp deploy/nginx.conf /etc/nginx/sites-available/ecommerce
sudo ln -s /etc/nginx/sites-available/ecommerce /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 4. TLS

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d example.com -d www.example.com
```

Or terminate TLS at an ALB with an ACM certificate — either way keep the
`X-Forwarded-Proto` header, which `SECURE_PROXY_SSL_HEADER` depends on.

### 5. Subsequent deploys

```bash
./deploy/deploy.sh
```

Pulls, installs, runs `check --deploy`, migrates, collects static, restarts
Gunicorn and reloads Nginx.

### Elastic Beanstalk

Set the platform to Python 3.11, `DJANGO_SETTINGS_MODULE=config.settings.prod`,
`WSGIPath=config.wsgi:application`, and all `.env` values as environment
properties. Add a container command for `migrate` and `collectstatic`.

### Deployment checklist

- [ ] `DJANGO_DEBUG=False` and a fresh `DJANGO_SECRET_KEY`
- [ ] `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` set to real domains
- [ ] `ADMIN_URL` changed from the default
- [ ] Real `PAYMENT_GATEWAY` credentials and `PAYMENT_WEBHOOK_SECRET`
- [ ] Webhook URL registered with the gateway
- [ ] RDS backups on; security groups locked down
- [ ] `python manage.py check --deploy` clean
- [ ] `collectstatic` in the pipeline
- [ ] `REDIS_URL` set if running more than one node

---

## Troubleshooting

**`django.db.utils.OperationalError: could not connect to server`**
**or `could not translate host name "host" to address`**
You have `DB_FALLBACK=False`, so a missing PostgreSQL server is an error by
design. Either start PostgreSQL and fix `DATABASE_URL`, or remove that setting
to let development fall back to SQLite automatically. (The second message means
`DATABASE_URL` is still the placeholder from `.env.example`, whose host is
literally `host`.)

**Homepage is empty**
No products are published. Run `python manage.py seed_data`, or add products in
the admin with status *Published*.

**Static files 404 in production**
Run `collectstatic`, and confirm the Nginx `alias` matches `STATIC_ROOT`.

**Payment always fails in development**
`PAYMENT_GATEWAY=mock` requires the signature the server generated. Use the
buttons on the payment page rather than crafting a request by hand.

**`No default throttle rate set for 'login' scope`**
You are running tests against non-test settings. Use `python manage.py test`
(which selects them automatically) or pass `--settings=config.settings.test`.

**`Cannot use ImageField because Pillow is not installed`**
Pillow failed to install, almost always because you are on Python 3.14. Rebuild
the venv against 3.12 (see [Dependencies and Python version](#dependencies-and-python-version)),
then re-run `pip install -r requirements.txt` and read its output for errors.

**`Fatal error in launcher: Unable to create process using ...`**
You are using a virtualenv copied from another machine. A venv is not portable —
the paths are baked into its `.exe` shims. Delete `venv/` and recreate it.

**`Failed building wheel` / `Microsoft Visual C++ 14.0 or greater is required`**
No prebuilt wheel exists for your Python version. Use Python 3.11-3.13.

**Search returns nothing on PostgreSQL**
Install the trigram extension: `CREATE EXTENSION IF NOT EXISTS pg_trgm;`
The code degrades gracefully if it is missing.

---

## License

Provided as-is for use as a project foundation. Review the security checklist
and replace all demo credentials before any public deployment.
