#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # ``manage.py test`` uses the dedicated test settings (fast hashing, no
    # rate limiting, isolated media) unless the caller passes --settings or
    # sets DJANGO_SETTINGS_MODULE explicitly.
    is_test = len(sys.argv) > 1 and sys.argv[1] == "test"
    explicit = any(arg.startswith("--settings") for arg in sys.argv)

    default_settings = "config.settings.test" if (is_test and not explicit) else "config.settings.dev"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", default_settings)

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
