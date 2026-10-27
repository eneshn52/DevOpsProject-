"""Local container HTTP configuration with room for synchronous model requests."""
import os

bind = '0.0.0.0:8000'
workers = 1
worker_class = 'gthread'
threads = 4
timeout = int(os.getenv('OLLAMA_REQUEST_TIMEOUT', '180')) + 60
graceful_timeout = timeout
accesslog = '-'
errorlog = '-'
forwarded_allow_ips = ''
