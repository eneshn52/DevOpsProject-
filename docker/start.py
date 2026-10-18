"""Apply pending schema migrations before replacing this process with Gunicorn."""
import os
import subprocess
import sys


def main():
    """Migrate the configured PostgreSQL database and launch the application server."""
    subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], check=True)
    os.execvp('gunicorn', ['gunicorn', '--config', 'gunicorn.conf.py', 'django_settings.wsgi:application'])


if __name__ == '__main__':
    main()
