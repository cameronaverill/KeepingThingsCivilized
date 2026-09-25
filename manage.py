#!/usr/bin/env python
"""Django's command-line utility."""
import os
import sys
from pathlib import Path


def main():
    # Load secrets and per-machine values from .env (real environment variables win over the file).
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
