"""Gunicorn configuration for the production WSGI server.

Run with:  gunicorn -c deploy/gunicorn.conf.py config.wsgi:application
"""
import multiprocessing
import os

# Bind to a unix socket when Nginx is on the same host (faster, no TCP stack);
# fall back to a port for container platforms that health-check over HTTP.
bind = os.environ.get("GUNICORN_BIND", "unix:/run/gunicorn/ecommerce.sock")

# The usual starting point; tune against real traffic, not guesses.
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"
threads = int(os.environ.get("GUNICORN_THREADS", 2))

timeout = 60
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically to bound the effect of any slow memory leak.
max_requests = 1000
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sus'

proc_name = "ecommerce"
preload_app = True

# Trust the reverse proxy in front of us for X-Forwarded-* headers.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
