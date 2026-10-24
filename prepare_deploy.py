#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Переносит базу и фотографии в папку data/ и собирает архив для сервера.

Запускать на домашнем компьютере, в папке с сайтом:

    python prepare_deploy.py

Получится два файла:
    aero_site.zip   — код сайта (загрузить в /srv/aero)
    aero_data.zip   — база и фотографии (загрузить в /srv/aero/data)

Их же можно загрузить через WinSCP вручную, архив просто удобнее.
"""

import os
import shutil
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "aero.db")
UPLOADS = os.path.join(BASE, "uploads")

# Файлы в корне сайта
CODE_FILES = [
    "app.py", "wsgi.py", "requirements.txt", "make_admin.py",
    "prepare_deploy.py", "README.txt",
]

# Папки целиком (deploy обходится этой же веткой — не дублируем)
CODE_DIRS = ["templates", "static", "deploy"]


def add_dir(zf, path, arc_prefix):
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, BASE)
            zf.write(full, rel)


def main():
    if not os.path.exists(DB):
        print("База не найдена: %s" % DB)
        print("Запустите сайт хотя бы раз, чтобы она создалась.")
        return 1

    # ── код ──
    site_zip = os.path.join(BASE, "aero_site.zip")
    written = set()
    with zipfile.ZipFile(site_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in CODE_FILES:
            full = os.path.join(BASE, name)
            if os.path.exists(full):
                zf.write(full, name)
                written.add(name)
        for name in CODE_DIRS:
            full = os.path.join(BASE, name)
            if os.path.isdir(full):
                for root, dirs, files in os.walk(full):
                    dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                    for f in files:
                        rel = os.path.relpath(os.path.join(root, f), BASE)
                        if rel not in written:
                            zf.write(os.path.join(root, f), rel)
                            written.add(rel)

    print("aero_site.zip  — код сайта, %d КБ" % (os.path.getsize(site_zip) // 1024))

    # ── данные ──
    data_zip = os.path.join(BASE, "aero_data.zip")
    count = 0
    with zipfile.ZipFile(data_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(DB, "aero.db")
        if os.path.isdir(UPLOADS):
            for root, dirs, files in os.walk(UPLOADS):
                for name in files:
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, BASE)
                    zf.write(full, rel)
                    count += 1

    print("aero_data.zip  — база и %d фото, %d КБ"
          % (count, os.path.getsize(data_zip) // 1024))

    # ── что дальше ──
    print("\nДальше:")
    print("  1. Загрузите aero_site.zip на сервер в /srv/aero и распакуйте")
    print("  2. Запустите: sudo bash deploy/install.sh ВАШ.ДОМЕН")
    print("  3. Загрузите aero_data.zip в /srv/aero/data и распакуйте")
    print("  4. sudo chown -R aero:aero /srv/aero/data")
    print("  5. sudo systemctl restart aero")
    print("\nПодробнее — в deploy/УСТАНОВКА.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
