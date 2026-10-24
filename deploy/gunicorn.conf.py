# Gunicorn — сервер приложений для сайта клуба.
# Запускается systemd, наружу не смотрит: запросы принимает nginx.

import multiprocessing

bind = "127.0.0.1:8001"

# Для одного ядра два воркера — с запасом. SQLite не любит много
# параллельных писателей, поэтому больше не нужно.
workers = 2
threads = 2

timeout = 60
graceful_timeout = 30
keepalive = 5

# Логи в journald (смотреть: journalctl -u aero -f)
accesslog = "-"
errorlog = "-"
loglevel = "warning"

# Перезапуск воркера, если потечёт память
max_requests = 800
max_requests_jitter = 100

proc_name = "aero"

# Приложение создаёт таблицы само при импорте (init_db в app.py),
# поэтому preload не нужен и даже вреден: каждый воркер должен
# иметь своё соединение с базой.
preload_app = False
