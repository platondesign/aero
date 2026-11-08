#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сайт авиамодельного клуба.

Название клуба настраивается в админке («О клубе» -> «Название клуба»),
поэтому в коде его нет.

Первый этап: новости, расписание, команда, профили с наградами,
личный кабинет, роли (админ / редактор / тренер / спортсмен).

Запуск:  python app.py
Сайт:    http://localhost:5000
"""

import io
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, abort, send_from_directory, send_file)
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Пути можно переопределить через окружение — удобно, когда база
# и фотографии лежат вне папки с кодом (так проще делать бэкап).
DB_PATH = os.environ.get("AERO_DB") or os.path.join(BASE_DIR, "aero.db")
UPLOAD_DIR = os.environ.get("AERO_UPLOADS") or os.path.join(BASE_DIR, "uploads")
PRIVATE_DIR = os.environ.get("AERO_PRIVATE") or os.path.join(BASE_DIR, "private")
SECRET_FILE = os.path.join(os.path.dirname(DB_PATH) or BASE_DIR, "secret.key")

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_FILE = 8 * 1024 * 1024          # 8 МБ на файл
MAX_REQUEST = 60 * 1024 * 1024      # 60 МБ на всю форму

try:
    from PIL import Image, ImageOps
    PIL_OK = True
except ImportError:                 # Pillow не установлен
    PIL_OK = False

ROLES = {
    "admin":   "Администратор",
    "editor":  "Редактор",
    "coach":   "Тренер",
    "athlete": "Спортсмен",
}
ROLE_ORDER = ["admin", "editor", "coach", "athlete"]

STATUSES = {
    "fly":     "Летает",
    "service": "На ТО",
    "build":   "В постройке",
    "scrap":   "Списан",
}

def load_secret():
    """Ключ подписи сессий.

    Порядок: переменная окружения -> файл secret.key -> создать новый.
    Файл создаётся один раз, чтобы сессии не слетали при перезапуске.
    """
    from_env = os.environ.get("AERO_SECRET")
    if from_env:
        return from_env

    try:
        with open(SECRET_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    except (OSError, IOError):
        pass

    key = secrets.token_hex(32)
    try:
        with open(SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(key)
        os.chmod(SECRET_FILE, 0o600)
    except (OSError, IOError):
        pass
    return key


app = Flask(__name__)
app.config["SECRET_KEY"] = load_secret()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST

# ─── сессия ───────────────────────────────────────────────────────────────────
# Без PERMANENT_SESSION_LIFETIME Flask ставит cookie «до закрытия браузера»,
# и после перезапуска браузера или телефона вход теряется. Делаем сессию
# постоянной на 30 дней; срок продлевается при каждом заходе.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_NAME"] = "aero_session"

# За nginx: доверяем одному прокси, чтобы видеть настоящий IP
# посетителя и понимать, что запрос пришёл по HTTPS.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

if os.environ.get("AERO_HTTPS", "1") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True


@app.before_request
def keep_session_alive():
    """Продлевать вход при каждом заходе.

    Флаг permanent ставится только для вошедших: анонимам постоянная cookie
    не нужна.
    """
    if session.get("uid"):
        session.permanent = True


# ─────────────────────────────────────────────────────────── база

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.after_request
def add_security_headers(resp):
    """Базовые заголовки безопасности для публичного сайта."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()")

    # Загруженные файлы отдаются как есть — запрещаем браузеру
    # угадывать тип и исполнять что-то из uploads/
    if request.path.startswith("/uploads/"):
        resp.headers["Content-Disposition"] = "inline"
        resp.headers["X-Content-Type-Options"] = "nosniff"

    return resp


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE,
    phone         TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    title         TEXT,
    bio           TEXT,
    photo         TEXT,
    birth_date    TEXT,
    is_approved   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role    TEXT NOT NULL,
    PRIMARY KEY (user_id, role)
);

CREATE TABLE IF NOT EXISTS news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    slug       TEXT UNIQUE NOT NULL,
    summary    TEXT,
    body       TEXT NOT NULL,
    cover      TEXT,
    author_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    published  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_photos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id  INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    starts_at  TEXT NOT NULL,
    ends_at    TEXT,
    place      TEXT,
    group_name TEXT,
    coach_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note       TEXT,
    capacity   INTEGER,
    series_id  TEXT,
    series_mode TEXT,
    series_days TEXT
);

CREATE TABLE IF NOT EXISTS awards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    event_name TEXT,
    place      INTEGER,
    year       INTEGER,
    note       TEXT
);

CREATE TABLE IF NOT EXISTS aircraft (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    class_code TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'fly',
    note       TEXT
);

CREATE TABLE IF NOT EXISTS event_signups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note       TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (event_id, user_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db():
    """Создаёт недостающие таблицы и папки.

    Безопасно вызывать при каждом запуске: все CREATE — с IF NOT EXISTS,
    существующие данные не трогаются. Так обновление версии
    не требует удалять базу.
    """
    for sub in ("news", "people", "share"):
        os.makedirs(os.path.join(UPLOAD_DIR, sub), exist_ok=True)

    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    # добавление колонок, появившихся в новых версиях
    have = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    for col, decl in (("photo", "TEXT"), ("birth_date", "TEXT"),
                      ("title", "TEXT"), ("bio", "TEXT"),
                      ("parent_consent", "TEXT")):
        if col not in have:
            db.execute("ALTER TABLE users ADD COLUMN %s %s" % (col, decl))

    have = {r[1] for r in db.execute("PRAGMA table_info(events)")}
    for col, decl in (("capacity", "INTEGER"), ("series_id", "TEXT"),
                      ("series_mode", "TEXT"), ("series_days", "TEXT")):
        if have and col not in have:
            db.execute("ALTER TABLE events ADD COLUMN %s %s" % (col, decl))

    db.commit()
    db.close()


# База готовится при каждом старте: и локально, и на сервере.
# Существующие данные не трогаются — только добавляется недостающее.
init_db()


# ─────────────────────────────────────────────────────────── фотографии

def _allowed(name):
    return os.path.splitext(name.lower())[1] in ALLOWED_EXT


def save_image(storage, kind, square=None, max_side=1600, quality=85):
    """Принимает файл из формы, приводит к JPEG, кладёт в uploads/<kind>/.

    square  — если задано, картинка обрезается по центру до квадрата
              (для аватаров).
    Возвращает (путь_относительно_uploads, ошибка).
    """
    if not storage or not getattr(storage, "filename", ""):
        return None, "файл не выбран"
    if not _allowed(storage.filename):
        return None, "поддерживаются JPG, PNG, WebP, GIF"
    if not PIL_OK:
        return None, "нужна библиотека Pillow: pip install pillow"

    data = storage.read()
    if len(data) > MAX_FILE:
        return None, "файл больше 8 МБ"
    if not data:
        return None, "пустой файл"

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        im = ImageOps.exif_transpose(im)     # поворот с телефона
    except Exception:
        return None, "не удалось прочитать изображение"

    if square:
        im = ImageOps.fit(im, (square, square), Image.LANCZOS,
                          centering=(0.5, 0.42))
    else:
        im.thumbnail((max_side, max_side), Image.LANCZOS)

    # прозрачность на белый фон
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")

    name = secrets.token_hex(12) + ".jpg"
    rel = kind + "/" + name
    full = os.path.join(UPLOAD_DIR, kind, name)

    # Папка под новый вид фото («share» и т.п.) может ещё не существовать —
    # init_db() готовит только news и people. Создаём на месте, иначе загрузка
    # падает с непонятным «не удалось сохранить изображение».
    try:
        os.makedirs(os.path.join(UPLOAD_DIR, kind), exist_ok=True)
    except OSError:
        return None, "не удалось создать папку для фото"

    try:
        im.save(full, "JPEG", quality=quality, optimize=True,
                progressive=True)
    except Exception:
        return None, "не удалось сохранить изображение"
    return rel, None


def delete_image(rel):
    """Удаляет файл из uploads. Молча, если его уже нет."""
    if not rel:
        return
    rel = str(rel).replace("\\", "/").lstrip("/")
    if rel.startswith("uploads/"):
        rel = rel[len("uploads/"):]
    if ".." in rel.split("/"):
        return
    full = os.path.join(UPLOAD_DIR, *rel.split("/"))
    try:
        if os.path.isfile(full):
            os.remove(full)
    except OSError:
        pass


def photos_for(news_id):
    return get_db().execute(
        "SELECT * FROM news_photos WHERE news_id = ? ORDER BY sort, id",
        (news_id,)).fetchall()


def cover_of(news_id):
    row = get_db().execute(
        "SELECT filename FROM news_photos WHERE news_id = ? "
        "ORDER BY sort, id LIMIT 1", (news_id,)).fetchone()
    return row["filename"] if row else None


# ─────────────────────────────────────────────────────────── утилиты

def slugify(text):
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    out = []
    for ch in text.lower():
        if ch in table:
            out.append(table[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("-")
    s = re.sub(r"-+", "-", "".join(out)).strip("-")
    return s or "novost"


def unique_slug(db, base):
    slug, n = base, 1
    while db.execute("SELECT 1 FROM news WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = "%s-%d" % (base, n)
    return slug


def parse_dt(value):
    """Строка даты -> datetime. Понимает все форматы, что пишем в базу.

    ISO с секундами и микросекундами тоже: именно так хранится
    created_at у новостей, и раньше из-за этого дата выводилась
    сырой строкой вместо «20 октября».
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # последняя попытка: обрезаем хвост после минут
    if "T" in text and len(text) >= 16:
        try:
            return datetime.strptime(text[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def news_date(value):
    """Дата новости по-человечески: «Сегодня», «Вчера», «5 октября»."""
    dt = _as_dt(value)
    if not dt:
        return ""

    today = date.today()
    delta = (today - dt.date()).days

    if delta == 0:
        return "Сегодня"
    if delta == 1:
        return "Вчера"
    if delta < 7:
        return "%d %s" % (delta, plural(delta, "день", "дня", "дней")) + " назад"

    months = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    out = "%d %s" % (dt.day, months[dt.month])
    if dt.year != today.year:
        out += " %d" % dt.year
    return out


def fmt_dt(value, with_time=True):
    if not value:
        return ""
    if isinstance(value, str):
        dt = parse_dt(value)
        if dt is None:
            return value
    else:
        dt = value
    months = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    s = "%d %s" % (dt.day, months[dt.month])
    if dt.year != datetime.now().year:
        s += " %d" % dt.year
    if with_time and (dt.hour or dt.minute):
        s += ", %02d:%02d" % (dt.hour, dt.minute)
    return s


MONTHS_SHORT = ["", "янв", "фев", "мар", "апр", "май", "июн",
                "июл", "авг", "сен", "окт", "ноя", "дек"]


def _as_dt(value):
    if not value:
        return None
    if isinstance(value, str):
        return parse_dt(value)
    return value


def ev_day(value):
    dt = _as_dt(value)
    return "%d" % dt.day if dt else ""


def ev_mon(value):
    dt = _as_dt(value)
    return MONTHS_SHORT[dt.month] if dt else ""


def ev_time(value):
    dt = _as_dt(value)
    return "%02d:%02d" % (dt.hour, dt.minute) if dt else ""


def ev_weekday(value):
    dt = _as_dt(value)
    if not dt:
        return ""
    names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    return names[dt.weekday()]


def ev_span(e):
    """Строка времени: «15:00 — 17:00» или просто «15:00». e — словарь/строка."""
    try:
        start = e["starts_at"]
        end = e["ends_at"] if "ends_at" in e.keys() else None
    except (TypeError, AttributeError):
        start, end = e, None
    a = ev_time(start)
    b = ev_time(end) if end else ""
    if a and b and a != b:
        return "%s — %s" % (a, b)
    return a


CLUB_NAME_DEFAULT = "Авиамодельный Клуб Волгодонска"
CLUB_SHORT_DEFAULT = "Авиамодельный"
CLUB_CITY_DEFAULT = "Волгодонск"

SETTING_DEFAULTS = {
    "club_name": CLUB_NAME_DEFAULT,
    "club_short": CLUB_SHORT_DEFAULT,
    "club_city": CLUB_CITY_DEFAULT,
    "about_text": "",
    "join_text": "",
    "contact_phone": "",
    "contact_email": "",
    "contact_address": "",
    "meeting_place": "",
    "site_url": "",
    "og_image": "",
}


def get_settings():
    """Тексты и контакты клуба, которые правятся из админки."""
    out = dict(SETTING_DEFAULTS)
    try:
        for row in get_db().execute("SELECT key, value FROM settings"):
            if row["key"] in out:
                out[row["key"]] = row["value"] or ""
    except sqlite3.Error:
        pass
    return out


# Поля, которые не должны оставаться пустыми: иначе сайт будет
# без названия. Если очистить — вернётся значение по умолчанию.
REQUIRED_SETTINGS = {
    "club_name": CLUB_NAME_DEFAULT,
    "club_short": CLUB_SHORT_DEFAULT,
    "club_city": CLUB_CITY_DEFAULT,
}


def save_settings(form):
    db = get_db()
    for key in SETTING_DEFAULTS:
        if key not in form:
            continue
        value = (form.get(key) or "").strip()
        if not value and key in REQUIRED_SETTINGS:
            value = REQUIRED_SETTINGS[key]
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))
    db.commit()


def safe_back(fallback):
    """Вернуться на страницу, с которой пришли. Только внутрь сайта."""
    back = (request.form.get("back") or "").strip()
    if back.startswith("/") and not back.startswith("//"):
        return back
    return fallback


# ─── адреса и метатеги для соцсетей ──────────────────────────────────────────

def base_url():
    """Внешний адрес сайта: из настроек или из текущего запроса.

    Нужен для абсолютных ссылок в og: и sitemap.xml — мессенджеры
    не понимают относительные пути.
    """
    cfg_url = (get_settings().get("site_url") or "").strip().rstrip("/")
    if cfg_url:
        if not cfg_url.startswith(("http://", "https://")):
            cfg_url = "https://" + cfg_url
        return cfg_url
    return request.url_root.rstrip("/")


def abs_url(path):
    """Сделать адрес абсолютным."""
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return base_url() + ("" if path.startswith("/") else "/") + path


def og_image_url():
    """Картинка для превью в мессенджерах.

    Берётся из настроек («О клубе» -> «Превью для соцсетей»), иначе — иконка
    приложения, чтобы ссылка никогда не разворачивалась в пустую заглушку.
    """
    name = (get_settings().get("og_image") or "").strip()
    if name:
        return abs_url("/uploads/" + name.lstrip("/"))
    return abs_url(url_for("static", filename="icon-512.png"))


def og_image_size():
    """Реальные размеры картинки превью.

    Мессенджеры верят og:image:width/height и обрезают картинку под эти цифры.
    Поэтому пишем настоящие, а не заглушку 1200×630.
    """
    name = (get_settings().get("og_image") or "").strip()
    if name:
        path = os.path.join(UPLOAD_DIR, name.lstrip("/"))
    else:
        path = os.path.join(BASE_DIR, "static", "icon-512.png")

    if PIL_OK:
        try:
            with Image.open(path) as im:
                return im.size
        except (OSError, ValueError):
            pass
    return (1200, 630) if name else (512, 512)


def clip(text, limit=200):
    """Обрезать текст описания по границе слова."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",.;:—-– ") + "…"


def meta_for(title=None, description=None, image=None, url=None, kind="website"):
    """Собрать метатеги страницы.

    Сами теги рисует base.html — здесь только значения. Каждая страница может
    передать свои заголовок и картинку, остальные берут общие из настроек.
    """
    cfg = get_settings()
    club = cfg.get("club_name") or CLUB_NAME_DEFAULT
    city = cfg.get("club_city") or CLUB_CITY_DEFAULT

    # Запасной вариант описания: если в админке пусто, всё равно нужен
    # осмысленный текст, иначе мессенджер покажет ссылку без подписи.
    fallback = ("%s — новости, расписание тренировок, команда и достижения. "
                "Авиамодельный спорт в %s." % (club, city))

    img = image or og_image_url()
    if image:
        w, h = 1200, 630
    else:
        w, h = og_image_size()

    return {
        "title": (title or club).strip(),
        "description": clip(description or cfg.get("about_text") or fallback),
        "image": img,
        "image_w": w,
        "image_h": h,
        "url": url or abs_url(request.path),
        "type": kind,
        "site_name": club,
    }


def roles_of(user_id):
    if not user_id:
        return []
    rows = get_db().execute(
        "SELECT role FROM user_roles WHERE user_id = ?", (user_id,)).fetchall()
    got = {r["role"] for r in rows}
    return [r for r in ROLE_ORDER if r in got]


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        session.clear()
        return None
    user = dict(row)
    user["roles"] = roles_of(uid)
    return user


def has_role(user, *wanted):
    if not user:
        return False
    return any(r in user["roles"] for r in wanted)


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            flash("Нужно войти в систему", "warn")
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def role_required(*wanted):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            user = current_user()
            if not user:
                flash("Нужно войти в систему", "warn")
                return redirect(url_for("login", next=request.path))
            if not has_role(user, *wanted):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


@app.context_processor
def inject():
    return {
        "user": current_user(),
        "S": get_settings(),
        "ROLES": ROLES,
        "STATUSES": STATUSES,
        "fmt_dt": fmt_dt,
        "news_date": news_date,
        "ev_day": ev_day,
        "ev_mon": ev_mon,
        "ev_time": ev_time,
        "ev_weekday": ev_weekday,
        "ev_span": ev_span,
        "now": datetime.now(),
        "meta": meta_for(),
    }


# ─────────────────────────────────────────────────────────── публичные страницы

@app.route("/")
def index():
    db = get_db()
    news = db.execute("""
        SELECT n.*, u.full_name AS author,
               (SELECT filename FROM news_photos p
                 WHERE p.news_id = n.id ORDER BY p.sort, p.id LIMIT 1) AS cover_file,
               (SELECT COUNT(*) FROM news_photos p WHERE p.news_id = n.id) AS n_photos
        FROM news n LEFT JOIN users u ON u.id = n.author_id
        WHERE n.published = 1
        ORDER BY n.created_at DESC LIMIT 3
    """).fetchall()
    events = db.execute("""
        SELECT e.*, u.full_name AS coach
        FROM events e LEFT JOIN users u ON u.id = e.coach_id
        WHERE e.starts_at >= ?
        ORDER BY e.starts_at ASC LIMIT 4
    """, (datetime.now().strftime("%Y-%m-%dT%H:%M"),)).fetchall()
    people = db.execute("""
        SELECT u.* FROM users u
        JOIN user_roles r ON r.user_id = u.id
        WHERE r.role IN ('coach','athlete') AND u.is_approved = 1
        ORDER BY u.full_name
    """).fetchall()
    return render_template("index.html", news=news, events=events,
                           people=people, meta=meta_for())


@app.route("/news")
def news_list():
    rows = get_db().execute("""
        SELECT n.*, u.full_name AS author,
               (SELECT filename FROM news_photos p
                 WHERE p.news_id = n.id ORDER BY p.sort, p.id LIMIT 1) AS cover_file,
               (SELECT COUNT(*) FROM news_photos p WHERE p.news_id = n.id) AS n_photos
        FROM news n LEFT JOIN users u ON u.id = n.author_id
        WHERE n.published = 1
        ORDER BY n.created_at DESC
    """).fetchall()
    return render_template("news.html", news=rows,
                           meta=meta_for(
                               title="Новости — %s" % get_settings()["club_name"],
                               description="Новости авиамодельного клуба: соревнования, "
                                           "тренировки и жизнь команды."))


@app.route("/news/<slug>")
def news_item(slug):
    row = get_db().execute("""
        SELECT n.*, u.full_name AS author
        FROM news n LEFT JOIN users u ON u.id = n.author_id
        WHERE n.slug = ?
    """, (slug,)).fetchone()
    if not row:
        abort(404)
    cover = cover_of(row["id"])
    return render_template("news_item.html", item=row,
                           photos=photos_for(row["id"]),
                           meta=meta_for(
                               title="%s — %s" % (row["title"],
                                                  get_settings()["club_name"]),
                               description=row["summary"] or row["body"],
                               image=abs_url("/uploads/" + cover) if cover else None,
                               url=abs_url(url_for("news_item", slug=row["slug"])),
                               kind="article"))


@app.route("/schedule")
def schedule():
    db = get_db()
    nowstr = datetime.now().strftime("%Y-%m-%dT%H:%M")
    upcoming = db.execute("""
        SELECT e.*, u.full_name AS coach
        FROM events e LEFT JOIN users u ON u.id = e.coach_id
        WHERE e.starts_at >= ? ORDER BY e.starts_at ASC
    """, (nowstr,)).fetchall()
    past = db.execute("""
        SELECT e.*, u.full_name AS coach
        FROM events e LEFT JOIN users u ON u.id = e.coach_id
        WHERE e.starts_at < ? ORDER BY e.starts_at DESC LIMIT 10
    """, (nowstr,)).fetchall()

    counts = {r["event_id"]: r["c"] for r in db.execute(
        "SELECT event_id, COUNT(*) c FROM event_signups GROUP BY event_id")}

    me = current_user()
    mine = set()
    if me:
        mine = {r["event_id"] for r in db.execute(
            "SELECT event_id FROM event_signups WHERE user_id = ?",
            (me["id"],))}

    # серия может дать десятки занятий — показываем ближайшие, остальные под катом
    LIMIT = 8
    shown = upcoming[:LIMIT]
    rest = upcoming[LIMIT:]
    rest_word = plural(len(rest), "занятие", "занятия", "занятий")

    return render_template("schedule.html", upcoming=shown, rest=rest,
                           rest_word=rest_word, total=len(upcoming),
                           past=past, counts=counts, mine=mine,
                           meta=meta_for(
                               title="Расписание тренировок — %s" % get_settings()["club_name"],
                               description="Ближайшие тренировки и занятия "
                                           "авиамодельного клуба."))


@app.route("/events/<int:eid>/join", methods=["POST"])
@login_required
def event_join(eid):
    db = get_db()
    me = current_user()
    ev = db.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    if not ev:
        abort(404)

    if db.execute("SELECT 1 FROM event_signups WHERE event_id = ? AND user_id = ?",
                  (eid, me["id"])).fetchone():
        return redirect(safe_back(url_for("schedule")))

    if ev["capacity"]:
        taken = db.execute(
            "SELECT COUNT(*) c FROM event_signups WHERE event_id = ?",
            (eid,)).fetchone()["c"]
        if taken >= ev["capacity"]:
            flash("На эту тренировку мест больше нет", "warn")
            return redirect(safe_back(url_for("schedule")))

    db.execute("""INSERT INTO event_signups (event_id, user_id, created_at)
                  VALUES (?, ?, ?)""",
               (eid, me["id"], datetime.now().isoformat(timespec="seconds")))
    db.commit()
    flash("Записались: " + ev["title"], "ok")
    return redirect(safe_back(url_for("schedule")))


@app.route("/events/<int:eid>/leave", methods=["POST"])
@login_required
def event_leave(eid):
    db = get_db()
    me = current_user()
    db.execute("DELETE FROM event_signups WHERE event_id = ? AND user_id = ?",
               (eid, me["id"]))
    db.commit()
    flash("Запись отменена", "warn")
    return redirect(safe_back(url_for("schedule")))


@app.route("/about")
def about():
    db = get_db()
    coaches = db.execute("""
        SELECT u.* FROM users u
        JOIN user_roles r ON r.user_id = u.id
        WHERE r.role = 'coach' AND u.is_approved = 1
        ORDER BY u.full_name
    """).fetchall()
    stats = {
        "people": db.execute(
            """SELECT COUNT(*) c FROM users u JOIN user_roles r
               ON r.user_id = u.id WHERE r.role = 'athlete'
               AND u.is_approved = 1""").fetchone()["c"],
        "news": db.execute(
            "SELECT COUNT(*) c FROM news WHERE published = 1").fetchone()["c"],
        "awards": db.execute("SELECT COUNT(*) c FROM awards").fetchone()["c"],
        "models": db.execute("SELECT COUNT(*) c FROM aircraft").fetchone()["c"],
    }
    return render_template("about.html", coaches=coaches, stats=stats,
                           meta=meta_for(
                               title="О клубе — %s" % get_settings()["club_name"]))


@app.route("/team")
def team():
    rows = get_db().execute("""
        SELECT u.* FROM users u
        JOIN user_roles r ON r.user_id = u.id
        WHERE r.role IN ('coach','athlete') AND u.is_approved = 1
        GROUP BY u.id
        ORDER BY u.full_name
    """).fetchall()
    people = []
    for r in rows:
        p = dict(r)
        p["roles"] = roles_of(p["id"])
        p["awards"] = get_db().execute(
            "SELECT COUNT(*) c FROM awards WHERE user_id = ?", (p["id"],)
        ).fetchone()["c"]
        p["aircraft"] = get_db().execute(
            "SELECT COUNT(*) c FROM aircraft WHERE user_id = ?", (p["id"],)
        ).fetchone()["c"]
        people.append(p)
    coaches = [p for p in people if "coach" in p["roles"]]
    athletes = [p for p in people if "athlete" in p["roles"]]
    return render_template("team.html", coaches=coaches, athletes=athletes,
                           meta=meta_for(
                               title="Команда — %s" % get_settings()["club_name"],
                               description="Тренерский состав и спортсмены "
                                           "авиамодельного клуба."))


@app.route("/u/<int:uid>")
def profile(uid):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        abort(404)
    person = dict(row)
    person["roles"] = roles_of(uid)

    viewer = current_user()
    is_owner = viewer and viewer["id"] == uid
    can_see_fleet = is_owner or has_role(viewer, "admin", "coach")
    if not person["is_approved"] and not is_owner and not has_role(viewer, "admin"):
        abort(404)

    awards = db.execute("""
        SELECT * FROM awards WHERE user_id = ?
        ORDER BY year DESC, place ASC
    """, (uid,)).fetchall()

    fleet, by_status = [], {}
    if can_see_fleet:
        fleet = db.execute(
            "SELECT * FROM aircraft WHERE user_id = ? ORDER BY class_code",
            (uid,)).fetchall()
        for s in STATUSES:
            by_status[s] = sum(1 for a in fleet if a["status"] == s)

    photo = person.get("photo")
    return render_template("profile.html", person=person, awards=awards,
                           fleet=fleet, by_status=by_status,
                           can_see_fleet=can_see_fleet, is_owner=is_owner,
                           meta=meta_for(
                               title="%s — %s" % (person["full_name"],
                                                  get_settings()["club_name"]),
                               description=person.get("bio") or person.get("title")
                                           or "Участник авиамодельного клуба.",
                               image=abs_url("/uploads/" + photo) if photo else None,
                               url=abs_url(url_for("profile", uid=uid)),
                               kind="profile"))


# ─────────────────────────────────────────────────────────── вход

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ident = (request.form.get("ident") or "").strip()
        pwd = request.form.get("password") or ""
        db = get_db()
        row = db.execute(
            "SELECT * FROM users WHERE email = ? OR phone = ?", (ident, ident)
        ).fetchone()
        if row and check_password_hash(row["password_hash"], pwd):
            if not row["is_approved"]:
                flash("Заявка ещё не одобрена администратором", "warn")
                return render_template("login.html")
            session.clear()
            session.permanent = True
            session["uid"] = row["id"]
            nxt = request.args.get("next") or url_for("cabinet")
            return redirect(nxt)
        flash("Неверный логин или пароль", "err")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        full_name = (f.get("full_name") or "").strip()
        email = (f.get("email") or "").strip().lower() or None
        phone = re.sub(r"[^\d+]", "", (f.get("phone") or "")) or None
        pwd = f.get("password") or ""
        pwd2 = f.get("password2") or ""

        err = None
        if not full_name:
            err = "Укажите имя и фамилию"
        elif not email and not phone:
            err = "Нужен телефон или почта"
        elif not (f.get("agree_docs") and f.get("agree_pd")):
            err = "Отметьте оба пункта: ознакомление с документами и согласие на обработку данных"
        elif len(pwd) < 8:
            err = "Пароль короче 8 символов"
        elif pwd != pwd2:
            err = "Пароли не совпадают"

        db = get_db()
        if not err and email and db.execute(
                "SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            err = "Эта почта уже зарегистрирована"
        if not err and phone and db.execute(
                "SELECT 1 FROM users WHERE phone = ?", (phone,)).fetchone():
            err = "Этот телефон уже зарегистрирован"

        if err:
            flash(err, "err")
            return render_template("register.html", form=f)

        cur = db.execute("""
            INSERT INTO users (email, phone, password_hash, full_name,
                               is_approved, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (email, phone, generate_password_hash(pwd), full_name,
              datetime.now().isoformat(timespec="seconds")))
        db.execute("INSERT INTO user_roles (user_id, role) VALUES (?, 'athlete')",
                   (cur.lastrowid,))
        db.commit()
        flash("Заявка отправлена. Администратор одобрит её — и вы сможете войти.",
              "ok")
        return redirect(url_for("login"))

    return render_template("register.html", form={})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ─────────────────────────────────────────────────────────── кабинет

@app.route("/cabinet")
@login_required
def cabinet():
    user = current_user()
    db = get_db()
    awards = db.execute(
        "SELECT * FROM awards WHERE user_id = ? ORDER BY year DESC",
        (user["id"],)).fetchall()
    fleet = db.execute(
        "SELECT * FROM aircraft WHERE user_id = ? ORDER BY class_code",
        (user["id"],)).fetchall()
    nowstr = datetime.now().strftime("%Y-%m-%dT%H:%M")
    upcoming = db.execute("""
        SELECT e.*, u.full_name AS coach FROM events e
        LEFT JOIN users u ON u.id = e.coach_id
        WHERE e.starts_at >= ? ORDER BY e.starts_at ASC LIMIT 5
    """, (nowstr,)).fetchall()

    my_events = db.execute("""
        SELECT e.*, s.id AS signup_id FROM event_signups s
        JOIN events e ON e.id = s.event_id
        WHERE s.user_id = ? AND e.starts_at >= ?
        ORDER BY e.starts_at ASC
    """, (user["id"], nowstr)).fetchall()

    return render_template("cabinet.html", me=user, awards=awards,
                           fleet=fleet, upcoming=upcoming,
                           my_events=my_events)


@app.route("/security")
@login_required
def security():
    return render_template("security.html", me=current_user())


@app.route("/security/password", methods=["POST"])
@login_required
def change_password():
    user = current_user()
    f = request.form
    current = f.get("current") or ""
    new = f.get("new") or ""
    again = f.get("new2") or ""

    db = get_db()
    row = db.execute("SELECT password_hash FROM users WHERE id = ?",
                     (user["id"],)).fetchone()

    if not check_password_hash(row["password_hash"], current):
        flash("Текущий пароль указан неверно", "err")
        return redirect(url_for("security"))
    if len(new) < 8:
        flash("Новый пароль короче 8 символов", "err")
        return redirect(url_for("security"))
    if new != again:
        flash("Пароли не совпадают", "err")
        return redirect(url_for("security"))
    if new == current:
        flash("Новый пароль совпадает с текущим", "warn")
        return redirect(url_for("security"))

    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (generate_password_hash(new), user["id"]))
    db.commit()
    flash("Пароль изменён", "ok")
    return redirect(url_for("security"))


@app.route("/cabinet/profile", methods=["POST"])
@login_required
def save_profile():
    user = current_user()
    f = request.form
    db = get_db()
    db.execute("""
        UPDATE users SET full_name = ?, title = ?, bio = ?, birth_date = ?
        WHERE id = ?
    """, ((f.get("full_name") or "").strip() or user["full_name"],
          (f.get("title") or "").strip(),
          (f.get("bio") or "").strip(),
          (f.get("birth_date") or "").strip() or None,
          user["id"]))
    db.commit()
    flash("Профиль сохранён", "ok")
    return redirect(url_for("cabinet"))


@app.route("/cabinet/aircraft/add", methods=["POST"])
@login_required
def add_aircraft():
    user = current_user()
    f = request.form
    name = (f.get("name") or "").strip()
    cls = (f.get("class_code") or "").strip().upper()
    st = f.get("status") or "fly"
    if name and cls:
        db = get_db()
        db.execute("""
            INSERT INTO aircraft (user_id, name, class_code, status, note)
            VALUES (?, ?, ?, ?, ?)
        """, (user["id"], name, cls, st if st in STATUSES else "fly",
              (f.get("note") or "").strip()))
        db.commit()
        flash("Модель добавлена", "ok")
    return redirect(url_for("cabinet"))


@app.route("/cabinet/aircraft/<int:aid>/delete", methods=["POST"])
@login_required
def del_aircraft(aid):
    user = current_user()
    db = get_db()
    db.execute("DELETE FROM aircraft WHERE id = ? AND user_id = ?",
               (aid, user["id"]))
    db.commit()
    return redirect(url_for("cabinet"))


@app.route("/cabinet/aircraft/<int:aid>/status", methods=["POST"])
@login_required
def set_aircraft_status(aid):
    user = current_user()
    st = request.form.get("status")
    if st in STATUSES:
        db = get_db()
        db.execute("UPDATE aircraft SET status = ? WHERE id = ? AND user_id = ?",
                   (st, aid, user["id"]))
        db.commit()
    return redirect(url_for("cabinet"))


# ─────────────────────────────────────────────────────────── редактор

@app.route("/editor")
@role_required("editor", "admin")
def editor():
    rows = get_db().execute("""
        SELECT n.*, u.full_name AS author,
               (SELECT COUNT(*) FROM news_photos p WHERE p.news_id = n.id) AS n_photos
        FROM news n
        LEFT JOIN users u ON u.id = n.author_id
        ORDER BY n.created_at DESC
    """).fetchall()
    return render_template("editor.html", news=rows)


@app.route("/editor/news/new", methods=["GET", "POST"])
@role_required("editor", "admin")
def news_new():
    return edit_news(None)


@app.route("/editor/news/<int:nid>", methods=["GET", "POST"])
@role_required("editor", "admin")
def news_edit(nid):
    return edit_news(nid)


def edit_news(nid):
    db = get_db()
    user = current_user()
    item = None
    if nid:
        item = db.execute("SELECT * FROM news WHERE id = ?", (nid,)).fetchone()
        if not item:
            abort(404)

    if request.method == "POST":
        f = request.form
        title = (f.get("title") or "").strip()
        body = (f.get("body") or "").strip()
        if not title or not body:
            flash("Нужны заголовок и текст", "err")
            return render_template("news_edit.html", item=item)
        published = 1 if f.get("published") else 0
        summary = (f.get("summary") or "").strip()

        if item:
            news_id = item["id"]
            db.execute("""UPDATE news SET title=?, summary=?, body=?,
                          published=? WHERE id=?""",
                       (title, summary, body, published, nid))
        else:
            slug = unique_slug(db, slugify(title))
            cur = db.execute("""INSERT INTO news (title, slug, summary, body,
                          author_id, published, created_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       (title, slug, summary, body, user["id"], published,
                        datetime.now().isoformat(timespec="seconds")))
            news_id = cur.lastrowid

        # ── новые фотографии ──
        files = request.files.getlist("photos")
        saved = 0
        for f in files:
            if not f or not f.filename:
                continue
            rel, err = save_image(f, "news", max_side=1600)
            if err:
                flash("Одно фото пропущено: " + err, "warn")
                continue
            nxt = db.execute(
                "SELECT COALESCE(MAX(sort), -1) + 1 s FROM news_photos "
                "WHERE news_id = ?", (news_id,)).fetchone()["s"]
            db.execute("INSERT INTO news_photos (news_id, filename, sort) "
                       "VALUES (?, ?, ?)", (news_id, rel, nxt))
            saved += 1

        db.commit()
        flash("Сохранено" + (" · фото: %d" % saved if saved else ""), "ok")
        return redirect(url_for("news_edit", nid=news_id))

    files = photos_for(item["id"]) if item else []
    return render_template("news_edit.html", item=item, photos=files)


@app.route("/editor/photo/<int:pid>/delete", methods=["POST"])
@role_required("editor", "admin")
def news_photo_delete(pid):
    db = get_db()
    row = db.execute("SELECT * FROM news_photos WHERE id = ?", (pid,)).fetchone()
    if not row:
        abort(404)
    db.execute("DELETE FROM news_photos WHERE id = ?", (pid,))
    db.commit()
    delete_image(row["filename"])
    return redirect(url_for("news_edit", nid=row["news_id"]))


@app.route("/editor/photo/<int:pid>/move/<direction>", methods=["POST"])
@role_required("editor", "admin")
def news_photo_move(pid, direction):
    if direction not in ("up", "down"):
        abort(404)
    db = get_db()
    row = db.execute("SELECT * FROM news_photos WHERE id = ?", (pid,)).fetchone()
    if not row:
        abort(404)
    nid = row["news_id"]
    ids = [r["id"] for r in db.execute(
        "SELECT id FROM news_photos WHERE news_id = ? ORDER BY sort, id",
        (nid,)).fetchall()]
    i = ids.index(pid)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    # перенумеровываем всё по порядку, чтобы старые одинаковые sort не мешали
    for k, x in enumerate(ids):
        db.execute("UPDATE news_photos SET sort = ? WHERE id = ?", (k, x))
    db.commit()
    return redirect(url_for("news_edit", nid=nid))


@app.route("/editor/news/<int:nid>/publish/<int:state>", methods=["POST"])
@role_required("editor", "admin")
def news_set_published(nid, state):
    if state not in (0, 1):
        abort(404)
    db = get_db()
    if not db.execute("SELECT 1 FROM news WHERE id = ?", (nid,)).fetchone():
        abort(404)
    db.execute("UPDATE news SET published = ? WHERE id = ?", (state, nid))
    db.commit()
    flash("Новость опубликована" if state else "Новость переведена в черновик", "ok")
    if request.form.get("next") == "list":
        return redirect(url_for("editor"))
    return redirect(url_for("news_edit", nid=nid))


@app.route("/editor/news/<int:nid>/delete", methods=["POST"])
@role_required("editor", "admin")
def news_delete(nid):
    db = get_db()
    for row in db.execute(
            "SELECT filename FROM news_photos WHERE news_id = ?", (nid,)):
        delete_image(row["filename"])
    db.execute("DELETE FROM news_photos WHERE news_id = ?", (nid,))
    db.execute("DELETE FROM news WHERE id = ?", (nid,))
    db.commit()
    flash("Новость удалена", "ok")
    return redirect(url_for("editor"))


# ─────────────────────────────────────────────────────────── админ

@app.route("/admin")
@role_required("admin")
def admin():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY is_approved, full_name").fetchall()
    people = []
    for u in users:
        p = dict(u)
        p["roles"] = roles_of(p["id"])
        people.append(p)
    pending = [p for p in people if not p["is_approved"]]
    admins = [p for p in people if "admin" in p["roles"] and p["is_approved"]]
    me = current_user()
    return render_template("admin.html", people=people, pending=pending,
                           admins=admins, me=me,
                           i_am_admin="admin" in me["roles"])


@app.route("/admin/users/<int:uid>/approve", methods=["POST"])
@role_required("admin")
def approve_user(uid):
    db = get_db()
    db.execute("UPDATE users SET is_approved = 1 WHERE id = ?", (uid,))
    db.commit()
    flash("Пользователь одобрен", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:uid>/roles", methods=["POST"])
@role_required("admin")
def set_roles(uid):
    db = get_db()
    user = current_user()
    chosen = [r for r in request.form.getlist("roles") if r in ROLES]

    before = set(roles_of(uid))
    losing_admin = "admin" in before and "admin" not in chosen

    if losing_admin:
        admins = db.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM user_roles WHERE role = 'admin'"
        ).fetchone()["c"]
        if admins <= 1:
            flash("Это единственный администратор. Сначала назначьте другого, "
                  "иначе управлять клубом будет некому.", "err")
            return redirect(url_for("admin"))
        if uid == user["id"]:
            flash("Вы снимаете роль администратора с самого себя. "
                  "Права применятся сразу — раздел «Админ» исчезнет.", "warn")

    db.execute("DELETE FROM user_roles WHERE user_id = ?", (uid,))
    for r in chosen:
        db.execute("INSERT INTO user_roles (user_id, role) VALUES (?, ?)",
                   (uid, r))
    db.commit()

    if chosen:
        flash("Роли обновлены: " + ", ".join(ROLES[r] for r in chosen), "ok")
    else:
        flash("Все роли сняты — у пользователя останется только личный кабинет",
              "warn")
    return redirect(url_for("admin"))


# ─────────────────────────────────────────────────────────── админ: тренировки

@app.route("/admin/events")
@role_required("admin", "coach")
def admin_events():
    db = get_db()
    rows = db.execute("""
        SELECT e.*, u.full_name AS coach
        FROM events e LEFT JOIN users u ON u.id = e.coach_id
        ORDER BY e.starts_at ASC
    """).fetchall()
    coaches = db.execute("""
        SELECT u.id, u.full_name FROM users u
        JOIN user_roles r ON r.user_id = u.id
        WHERE r.role = 'coach' AND u.is_approved = 1
        ORDER BY u.full_name
    """).fetchall()
    counts = {r["event_id"]: r["c"] for r in db.execute(
        "SELECT event_id, COUNT(*) c FROM event_signups GROUP BY event_id")}

    # сколько занятий в каждой серии — для пометки в таблице
    series_size = {r["series_id"]: r["c"] for r in db.execute(
        "SELECT series_id, COUNT(*) c FROM events "
        "WHERE series_id IS NOT NULL GROUP BY series_id")}

    # на странице не нужны все 50 занятий одной серии: показываем
    # первое из каждой серии + все одиночные
    seen, compact = set(), []
    for e in rows:
        sid = e["series_id"]
        if not sid:
            compact.append(e)
            continue
        if sid in seen:
            continue
        seen.add(sid)
        compact.append(e)

    # сколько записей «висит» на каждой серии — для предупреждения при удалении
    series_signups = {}
    for r in db.execute("""
        SELECT e.series_id AS sid, COUNT(s.id) AS c
        FROM events e JOIN event_signups s ON s.event_id = e.id
        WHERE e.series_id IS NOT NULL GROUP BY e.series_id
    """):
        series_signups[r["sid"]] = r["c"]

    return render_template("admin_events.html", events=compact, coaches=coaches,
                           counts=counts, series_size=series_size,
                           series_signups=series_signups,
                           repeat_modes=REPEAT_MODES,
                           weekday_names=WEEKDAY_NAMES)


@app.route("/admin/events/<int:eid>")
@role_required("admin", "coach")
def admin_event_detail(eid):
    db = get_db()
    ev = db.execute("""
        SELECT e.*, u.full_name AS coach FROM events e
        LEFT JOIN users u ON u.id = e.coach_id WHERE e.id = ?
    """, (eid,)).fetchone()
    if not ev:
        abort(404)
    people = db.execute("""
        SELECT u.*, s.id AS signup_id, s.created_at AS signed_at
        FROM event_signups s JOIN users u ON u.id = s.user_id
        WHERE s.event_id = ? ORDER BY s.created_at
    """, (eid,)).fetchall()
    return render_template("admin_event_detail.html", ev=ev, people=people)


@app.route("/admin/events/<int:eid>/signup/<int:sid>/delete", methods=["POST"])
@role_required("admin", "coach")
def admin_signup_delete(eid, sid):
    db = get_db()
    db.execute("DELETE FROM event_signups WHERE id = ? AND event_id = ?",
               (sid, eid))
    db.commit()
    flash("Участник снят с записи", "ok")
    return redirect(url_for("admin_event_detail", eid=eid))


REPEAT_MODES = {
    "none":     "Не повторять",
    "weekly":   "Каждую неделю",
    "biweekly": "Раз в две недели",
    "weekdays": "По дням недели",
    "daily":    "Каждый день",
}
WEEKDAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SERIES_LIMIT = 400          # предохранитель от «каждый день на 10 лет»


def plural(n, one, few, many):
    """1 занятие / 2 занятия / 5 занятий"""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


LEGACY_MODES = {v: k for k, v in REPEAT_MODES.items()}
SERIES_STEP = {"daily": 1, "weekly": 7, "biweekly": 14}


def series_key(row):
    """Режим серии. Старые записи хранили русскую подпись — понимаем и её."""
    m = (row["series_mode"] or "").strip()
    if m in REPEAT_MODES:
        return m
    return LEGACY_MODES.get(m, "weekdays")


def series_weekdays(row, events=None):
    """Дни недели серии: из базы, иначе вычисляем по самим занятиям."""
    stored = (row["series_days"] or "").strip()
    if stored:
        days = {int(x) for x in stored.split(",") if x.strip().isdigit()}
        if days:
            return days
    if events:
        return {parse_dt(e["starts_at"]).weekday() for e in events}
    return set()


def hhmm(value):
    m = re.match(r"^(\d{1,2}):(\d{2})$", (value or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h, mi
    return None


def extend_dates(last_dt, mode, weekdays, until, existing, limit=SERIES_LIMIT):
    """Даты, которые нужно ДОБАВИТЬ к серии после last_dt."""
    out = []

    if mode == "weekdays":
        if not weekdays:
            return out
        day = last_dt.date() + timedelta(days=1)
        while day <= until.date() and len(out) < limit:
            if day.weekday() in weekdays:
                d = datetime.combine(day, last_dt.time())
                if d not in existing:
                    out.append(d)
            day += timedelta(days=1)
        return out

    step = SERIES_STEP.get(mode)
    if not step:
        return out
    cur = last_dt + timedelta(days=step)
    while cur.date() <= until.date() and len(out) < limit:
        if cur not in existing:
            out.append(cur)
        cur = cur + timedelta(days=step)
    return out


def build_series(start, mode, until, weekdays):
    """Список дат для серии. start — datetime первого занятия.

    Возвращает (даты, ошибка). Первая дата всегда = start.
    """
    if mode == "none":
        return [start], None

    if not until:
        return None, "Укажите дату, до которой повторять"

    end = until
    if end < start:
        return None, "Дата окончания раньше даты начала"

    dates = []
    if mode == "weekdays":
        if not weekdays:
            return None, "Отметьте хотя бы один день недели"
        day = start.date()
        while day <= end.date() and len(dates) < SERIES_LIMIT:
            if day.weekday() in weekdays:
                dates.append(datetime.combine(day, start.time()))
            day = day + timedelta(days=1)
        if not dates:
            return None, "В выбранные дни нет ни одной даты в этом промежутке"
        return dates, None

    step = {"daily": 1, "weekly": 7, "biweekly": 14}.get(mode)
    if not step:
        return None, "Неизвестный тип повтора"

    cur = start
    while cur.date() <= end.date() and len(dates) < SERIES_LIMIT:
        dates.append(cur)
        cur = cur + timedelta(days=step)
    return dates, None



@app.route("/admin/events/save", methods=["POST"])
@role_required("admin", "coach")
def admin_event_save():
    f = request.form
    eid = f.get("id")
    title = (f.get("title") or "").strip()
    starts = (f.get("starts_at") or "").strip()
    ends = (f.get("ends_at") or "").strip() or None
    place = (f.get("place") or "").strip()
    group = (f.get("group_name") or "").strip()
    note = (f.get("note") or "").strip()
    coach_id = f.get("coach_id") or None
    capacity = f.get("capacity") or None
    if capacity:
        try:
            capacity = int(capacity)
        except (TypeError, ValueError):
            capacity = None

    if not title or not parse_dt(starts):
        flash("Нужны название и корректная дата начала", "err")
        return redirect(url_for("admin_events"))

    db = get_db()

    # ── правка существующего занятия: серию не трогаем ──
    if eid:
        db.execute("""UPDATE events SET title=?, starts_at=?, ends_at=?,
                      place=?, group_name=?, note=?, coach_id=?, capacity=?
                      WHERE id=?""",
                   (title, starts, ends, place, group, note, coach_id,
                    capacity, eid))
        db.commit()
        flash("Тренировка обновлена", "ok")
        return redirect(url_for("admin_events"))

    # ── новая тренировка ──
    mode = (f.get("repeat") or "none").strip()
    until_raw = (f.get("repeat_until") or "").strip()
    weekdays = {int(x) for x in f.getlist("repeat_days")
                if str(x).isdigit() and 0 <= int(x) <= 6}

    if mode == "none":
        db.execute("""INSERT INTO events (title, starts_at, ends_at, place,
                      group_name, note, coach_id, capacity)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (title, starts, ends, place, group, note, coach_id,
                    capacity))
        db.commit()
        flash("Тренировка добавлена", "ok")
        return redirect(url_for("admin_events"))

    until = parse_dt(until_raw) if until_raw else None
    if until and len(until_raw) == 10:
        until = datetime.combine(until.date(), datetime.max.time())

    dates, err = build_series(parse_dt(starts), mode, until, weekdays)
    if err:
        flash("Серия не создана: " + err, "err")
        return redirect(url_for("admin_events"))

    series = secrets.token_hex(8)
    first = parse_dt(starts)
    end_dt = parse_dt(ends) if ends else None
    span = (end_dt - first) if end_dt else None
    days_str = ",".join(str(d) for d in sorted(weekdays)) or None

    for d in dates:
        e_end = (d + span).strftime("%Y-%m-%dT%H:%M") if span else None
        db.execute("""INSERT INTO events (title, starts_at, ends_at, place,
                      group_name, note, coach_id, capacity,
                      series_id, series_mode, series_days)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (title, d.strftime("%Y-%m-%dT%H:%M"), e_end, place,
                    group, note, coach_id, capacity, series, mode, days_str))
    db.commit()

    last = dates[-1]
    flash("Создана серия: %d %s, с %s по %s"
          % (len(dates), plural(len(dates), "занятие", "занятия", "занятий"),
             fmt_dt(dates[0], with_time=False),
             fmt_dt(last, with_time=False)), "ok")
    return redirect(url_for("admin_events"))


@app.route("/admin/events/<int:eid>/delete", methods=["POST"])
@role_required("admin", "coach")
def admin_event_delete(eid):
    db = get_db()
    scope = (request.form.get("scope") or "one").strip()

    row = db.execute("SELECT series_id FROM events WHERE id = ?",
                     (eid,)).fetchone()
    if not row:
        abort(404)

    if scope == "series" and row["series_id"]:
        n = db.execute("SELECT COUNT(*) c FROM events WHERE series_id = ?",
                       (row["series_id"],)).fetchone()["c"]
        db.execute("DELETE FROM events WHERE series_id = ?",
                   (row["series_id"],))
        db.commit()
        flash("Удалена серия: %d %s"
              % (n, plural(n, "занятие", "занятия", "занятий")), "ok")
    else:
        db.execute("DELETE FROM events WHERE id = ?", (eid,))
        db.commit()
        flash("Удалено", "ok")
    return redirect(url_for("admin_events"))


# ─────────────────────────────────────────────────────────── админ: награды

def refresh_series_days(db, sid):
    """После сдвига дат пересчитывает дни недели серии."""
    rows = db.execute("SELECT starts_at FROM events WHERE series_id = ?",
                      (sid,)).fetchall()
    days = sorted({parse_dt(r["starts_at"]).weekday() for r in rows})
    db.execute("UPDATE events SET series_days = ? WHERE series_id = ?",
               (",".join(str(d) for d in days) or None, sid))
    db.commit()


def count_clashes(db, sid):
    """Сколько занятий серии совпало по времени с другими тренировками."""
    row = db.execute("""
        SELECT COUNT(*) c FROM events a
        JOIN events b ON b.starts_at = a.starts_at AND b.id <> a.id
        WHERE a.series_id = ?
          AND (b.series_id IS NULL OR b.series_id <> a.series_id)
    """, (sid,)).fetchone()
    return row["c"] if row else 0


@app.route("/admin/series/<sid>", methods=["GET", "POST"])
@role_required("admin", "coach")
def admin_series(sid):
    """Правка всей серии. Занятия не пересоздаются — записи сохраняются."""
    db = get_db()

    def load():
        return db.execute("""
            SELECT e.*, u.full_name AS coach,
                   (SELECT COUNT(*) FROM event_signups s
                     WHERE s.event_id = e.id) AS n_signups
            FROM events e LEFT JOIN users u ON u.id = e.coach_id
            WHERE e.series_id = ? ORDER BY e.starts_at
        """, (sid,)).fetchall()

    events = load()
    if not events:
        abort(404)

    # режим и дни недели нужны и в GET, и в POST
    first = events[0]
    mode = series_key(first)
    weekdays = series_weekdays(first, events)

    coaches = db.execute("""
        SELECT u.id, u.full_name FROM users u
        JOIN user_roles r ON r.user_id = u.id
        WHERE r.role = 'coach' AND u.is_approved = 1
        ORDER BY u.full_name
    """).fetchall()

    if request.method == "POST":
        f = request.form
        action = (f.get("action") or "").strip()

        # ── общие поля: даты не трогаем, записи целы ──
        if action == "fields":
            title = (f.get("title") or "").strip()
            if not title:
                flash("Название не может быть пустым", "err")
                return redirect(url_for("admin_series", sid=sid))

            capacity = f.get("capacity") or None
            if capacity:
                try:
                    capacity = int(capacity)
                except (TypeError, ValueError):
                    capacity = None
            else:
                capacity = None

            db.execute("""UPDATE events SET title=?, place=?, group_name=?,
                          note=?, coach_id=?, capacity=?
                          WHERE series_id=?""",
                       (title, (f.get("place") or "").strip(),
                        (f.get("group_name") or "").strip(),
                        (f.get("note") or "").strip(),
                        f.get("coach_id") or None, capacity, sid))
            db.commit()
            flash("Обновлено занятий: %d. Даты не менялись — записи на месте"
                  % len(events), "ok")
            return redirect(url_for("admin_series", sid=sid))

        # ── время: даты те же, меняется только час ──
        if action == "time":
            start_hm = hhmm(f.get("start_time"))
            end_hm = hhmm(f.get("end_time"))
            if not start_hm:
                flash("Укажите время начала, например 15:00", "err")
                return redirect(url_for("admin_series", sid=sid))

            for e in events:
                dt = parse_dt(e["starts_at"])
                dur = None
                if e["ends_at"]:
                    dur = parse_dt(e["ends_at"]) - dt

                new_start = dt.replace(hour=start_hm[0], minute=start_hm[1])
                if end_hm:
                    new_end = new_start.replace(hour=end_hm[0], minute=end_hm[1])
                elif dur is not None:
                    new_end = new_start + dur
                else:
                    new_end = None

                db.execute("""UPDATE events SET starts_at=?, ends_at=?
                              WHERE id=?""",
                           (new_start.strftime("%Y-%m-%dT%H:%M"),
                            new_end.strftime("%Y-%m-%dT%H:%M") if new_end else None,
                            e["id"]))
            db.commit()
            flash("Время обновлено у %d занятий. Записи сохранены" % len(events), "ok")
            return redirect(url_for("admin_series", sid=sid))

        # ── перенос всей серии на другие даты ──
        if action == "dates":
            raw = (f.get("new_start") or "").strip()
            if not raw:
                flash("Укажите новую дату первого занятия", "err")
                return redirect(url_for("admin_series", sid=sid))

            try:
                new_first = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                flash("Дата непонятна", "err")
                return redirect(url_for("admin_series", sid=sid))

            old_first = parse_dt(first["starts_at"])
            delta = (new_first.date() - old_first.date()).days

            if delta == 0:
                flash("Дата не изменилась", "warn")
                return redirect(url_for("admin_series", sid=sid))

            # каждое занятие сдвигается на одно и то же число дней:
            # расстояния внутри серии сохраняются, записи остаются
            for e in events:
                st = parse_dt(e["starts_at"]) + timedelta(days=delta)
                en = (parse_dt(e["ends_at"]) + timedelta(days=delta)
                      if e["ends_at"] else None)
                db.execute("""UPDATE events SET starts_at=?, ends_at=?
                              WHERE id=?""",
                           (st.strftime("%Y-%m-%dT%H:%M"),
                            en.strftime("%Y-%m-%dT%H:%M") if en else None,
                            e["id"]))
            db.commit()

            if mode == "weekdays":
                refresh_series_days(db, sid)

            moved = load()
            clashes = count_clashes(db, sid)

            flash("Серия перенесена на %d %s: теперь с %s по %s" % (
                abs(delta), plural(abs(delta), "день", "дня", "дней"),
                fmt_dt(moved[0]["starts_at"], with_time=False),
                fmt_dt(moved[-1]["starts_at"], with_time=False)), "ok")

            if clashes:
                flash("Внимание: %d %s совпадает по времени с другими "
                      "тренировками. Проверьте расписание." %
                      (clashes, plural(clashes, "занятие", "занятия",
                                       "занятий")), "warn")
            return redirect(url_for("admin_series", sid=sid))

        # ── перенос одного занятия ──
        if action == "one_date":
            eid = f.get("event_id")
            target = None
            for e in events:
                if str(e["id"]) == str(eid):
                    target = e
                    break
            if not target:
                flash("Занятие не найдено", "err")
                return redirect(url_for("admin_series", sid=sid))

            raw = (f.get("one_date") or "").strip()
            if not raw:
                flash("Укажите новую дату занятия", "err")
                return redirect(url_for("admin_series", sid=sid))

            try:
                new_day = datetime.strptime(raw[:10], "%Y-%m-%d")
            except ValueError:
                flash("Дата непонятна", "err")
                return redirect(url_for("admin_series", sid=sid))

            old_dt = parse_dt(target["starts_at"])
            hm = hhmm(f.get("one_time")) or (old_dt.hour, old_dt.minute)
            new_dt = new_day.replace(hour=hm[0], minute=hm[1])

            dur = None
            if target["ends_at"]:
                dur = parse_dt(target["ends_at"]) - old_dt
            new_end = (new_dt + dur) if dur else None

            if new_dt == old_dt:
                flash("Дата и время не изменились", "warn")
                return redirect(url_for("admin_series", sid=sid))

            db.execute("""UPDATE events SET starts_at=?, ends_at=?
                          WHERE id=?""",
                       (new_dt.strftime("%Y-%m-%dT%H:%M"),
                        new_end.strftime("%Y-%m-%dT%H:%M") if new_end else None,
                        target["id"]))
            db.commit()

            if mode == "weekdays":
                refresh_series_days(db, sid)

            if new_dt.date() == old_dt.date():
                flash("Время занятия изменено на %s. Записи сохранены"
                      % fmt_dt(new_dt), "ok")
            else:
                flash("%s перенесено на %s. Записи сохранены" % (
                    fmt_dt(old_dt, with_time=False), fmt_dt(new_dt)), "ok")
            return redirect(url_for("admin_series", sid=sid))

        # ── продлить серию ──
        if action == "extend":
            raw = (f.get("until") or "").strip()
            until = parse_dt(raw) if raw else None
            if not until:
                flash("Укажите дату, до которой продлить", "err")
                return redirect(url_for("admin_series", sid=sid))
            if len(raw) == 10:
                until = datetime.combine(until.date(), datetime.max.time())

            last_dt = parse_dt(events[-1]["starts_at"])
            if until <= last_dt:
                flash("Эта дата не позже последнего занятия — продлевать нечего",
                      "warn")
                return redirect(url_for("admin_series", sid=sid))

            existing = {parse_dt(e["starts_at"]) for e in events}
            wd = series_weekdays(first, events)
            new_dates = extend_dates(last_dt, mode, wd, until, existing)

            if not new_dates:
                flash("Новых занятий не получилось: проверьте дни недели серии",
                      "warn")
                return redirect(url_for("admin_series", sid=sid))

            dur = None
            if first["ends_at"]:
                dur = parse_dt(first["ends_at"]) - parse_dt(first["starts_at"])

            for d in new_dates:
                e_end = (d + dur).strftime("%Y-%m-%dT%H:%M") if dur else None
                db.execute("""INSERT INTO events (title, starts_at, ends_at,
                              place, group_name, note, coach_id, capacity,
                              series_id, series_mode, series_days)
                              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (first["title"], d.strftime("%Y-%m-%dT%H:%M"), e_end,
                            first["place"], first["group_name"], first["note"],
                            first["coach_id"], first["capacity"], sid, mode,
                            first["series_days"]))
            db.commit()
            flash("Серия продлена: добавлено %d %s"
                  % (len(new_dates),
                     plural(len(new_dates), "занятие", "занятия", "занятий")),
                  "ok")
            return redirect(url_for("admin_series", sid=sid))

        # ── сократить серию ──
        if action == "cut":
            raw = (f.get("cut_after") or "").strip()
            if not raw:
                flash("Укажите дату, после которой удалить занятия", "err")
                return redirect(url_for("admin_series", sid=sid))
            cutoff = raw[:10]

            victims = [e for e in events if e["starts_at"][:10] > cutoff]
            if not victims:
                flash("После этой даты занятий нет", "warn")
                return redirect(url_for("admin_series", sid=sid))
            if len(victims) == len(events):
                flash("Так удалится вся серия. Для этого есть кнопка «Удалить "
                      "всю серию» в списке тренировок", "err")
                return redirect(url_for("admin_series", sid=sid))

            lost = sum(v["n_signups"] for v in victims)
            for v in victims:
                db.execute("DELETE FROM events WHERE id = ?", (v["id"],))
            db.commit()

            msg = "Удалено %d %s" % (len(victims),
                                     plural(len(victims), "занятие", "занятия",
                                            "занятий"))
            if lost:
                msg += ", вместе с ними пропало %d %s" % (
                    lost, plural(lost, "запись", "записи", "записей"))
            flash(msg, "warn")
            return redirect(url_for("admin_series", sid=sid))

        flash("Неизвестное действие", "err")
        return redirect(url_for("admin_series", sid=sid))

    total_signups = sum(e["n_signups"] for e in events)
    return render_template("admin_series.html", sid=sid, events=events,
                           coaches=coaches, mode=mode,
                           mode_label=REPEAT_MODES.get(mode, mode),
                           weekdays=weekdays,
                           weekday_names=WEEKDAY_NAMES,
                           total_signups=total_signups)


@app.route("/admin/awards")
@role_required("admin", "coach")
def admin_awards():
    db = get_db()
    rows = db.execute("""
        SELECT a.*, u.full_name AS who FROM awards a
        JOIN users u ON u.id = a.user_id
        ORDER BY a.year DESC, a.place ASC
    """).fetchall()
    people = db.execute("""
        SELECT u.id, u.full_name FROM users u
        WHERE u.is_approved = 1
        ORDER BY u.full_name
    """).fetchall()
    return render_template("admin_awards.html", awards=rows, people=people)


@app.route("/admin/awards/save", methods=["POST"])
@role_required("admin", "coach")
def admin_award_save():
    f = request.form
    aid = f.get("id")
    uid = f.get("user_id")
    title = (f.get("title") or "").strip()
    event_name = (f.get("event_name") or "").strip()
    place = f.get("place") or None
    year = f.get("year") or None
    note = (f.get("note") or "").strip()

    if not uid or not title:
        flash("Выберите спортсмена и укажите награду", "err")
        return redirect(url_for("admin_awards"))

    db = get_db()
    if aid:
        db.execute("""UPDATE awards SET user_id=?, title=?, event_name=?,
                      place=?, year=?, note=? WHERE id=?""",
                   (uid, title, event_name, place, year, note, aid))
        flash("Награда обновлена", "ok")
    else:
        db.execute("""INSERT INTO awards (user_id, title, event_name,
                      place, year, note) VALUES (?,?,?,?,?,?)""",
                   (uid, title, event_name, place, year, note))
        flash("Награда добавлена", "ok")
    db.commit()
    return redirect(url_for("admin_awards"))


@app.route("/admin/awards/<int:aid>/delete", methods=["POST"])
@role_required("admin", "coach")
def admin_award_delete(aid):
    db = get_db()
    db.execute("DELETE FROM awards WHERE id = ?", (aid,))
    db.commit()
    flash("Удалено", "ok")
    return redirect(url_for("admin_awards"))


@app.route("/admin/about", methods=["GET", "POST"])
@role_required("admin")
def admin_about():
    db = get_db()
    if request.method == "POST":
        section = (request.form.get("section") or "").strip()

        # ── превью для соцсетей: сохраняем картинку отдельно от текстов ──
        if section == "share":
            site_url = (request.form.get("site_url") or "").strip().rstrip("/")
            db.execute(
                "INSERT INTO settings (key, value) VALUES ('site_url', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (site_url,))
            db.commit()

            if request.form.get("og_clear"):
                old = db.execute(
                    "SELECT value FROM settings WHERE key = 'og_image'"
                ).fetchone()
                if old and old["value"]:
                    delete_image(old["value"])
                db.execute("DELETE FROM settings WHERE key = 'og_image'")
                db.commit()
                flash("Картинка превью убрана", "ok")

            upload = request.files.get("og_image_file")
            if upload and getattr(upload, "filename", ""):
                rel, err = save_image(upload, "share", max_side=1200)
                if err:
                    flash("Картинка: " + err, "err")
                else:
                    db.execute(
                        "INSERT INTO settings (key, value) VALUES ('og_image', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (rel,))
                    db.commit()
                    flash("Картинка превью обновлена", "ok")

            flash("Настройки превью сохранены", "ok")
            return redirect(url_for("admin_about"))

        save_settings(request.form)
        if (request.form.get("section") or "") == "name":
            name = (request.form.get("club_name") or "").strip()
            flash("Название обновлено: %s. Если сайт установлен на телефон "
                  "как приложение, имя сменится после переустановки"
                  % (name or CLUB_NAME_DEFAULT), "ok")
        else:
            flash("Страница «О клубе» сохранена", "ok")
        return redirect(url_for("admin_about"))
    return render_template("admin_about.html", cfg=get_settings())


# ─────────────────────────────────────────────────────────── API v1

API_VERSION = "v1"


def api_json(data, code=200):
    from flask import jsonify
    resp = jsonify(data)
    resp.status_code = code
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def api_error(msg, code=400):
    return api_json({"ok": False, "error": msg}, code)


def api_person(row, with_fleet=False):
    """Публичное представление человека. Без пароля и контактов."""
    p = {
        "id": row["id"],
        "name": row["full_name"],
        "title": row["title"] or "",
        "bio": row["bio"] or "",
        "photo": row["photo"] or "",
        "roles": roles_of(row["id"]),
    }
    if with_fleet:
        db = get_db()
        fleet = db.execute(
            "SELECT * FROM aircraft WHERE user_id = ? ORDER BY class_code",
            (row["id"],)).fetchall()
        p["fleet"] = [{
            "id": a["id"], "name": a["name"],
            "class": a["class_code"], "status": a["status"],
        } for a in fleet]
        p["fleet_counts"] = {
            s: sum(1 for a in fleet if a["status"] == s) for s in STATUSES
        }
    return p


@app.route("/api/v1/")
def api_root():
    return api_json({
        "ok": True,
        "service": get_settings()["club_name"] or CLUB_NAME_DEFAULT,
        "api": API_VERSION,
        "endpoints": {
            "GET /api/v1/news": "список новостей",
            "GET /api/v1/news/<slug>": "одна новость",
            "GET /api/v1/schedule": "расписание (upcoming / past)",
            "GET /api/v1/team": "участники клуба",
            "GET /api/v1/people/<id>": "профиль участника",
            "POST /api/v1/login": "вход, возвращает токен",
            "GET /api/v1/me": "свой профиль (нужен токен)",
            "GET /api/v1/me/fleet": "свой парк моделей (нужен токен)",
        },
    })


@app.route("/api/v1/news")
def api_news():
    db = get_db()
    limit = request.args.get("limit", type=int)
    sql = """SELECT n.*, u.full_name AS author FROM news n
             LEFT JOIN users u ON u.id = n.author_id
             WHERE n.published = 1 ORDER BY n.created_at DESC"""
    rows = db.execute(sql).fetchall()
    if limit:
        rows = rows[:limit]
    return api_json({
        "ok": True,
        "count": len(rows),
        "items": [{
            "id": n["id"],
            "title": n["title"],
            "slug": n["slug"],
            "summary": n["summary"] or "",
            "date": n["created_at"],
            "author": n["author"] or "",
            "url": "/news/" + n["slug"],
        } for n in rows],
    })


@app.route("/api/v1/news/<slug>")
def api_news_one(slug):
    db = get_db()
    n = db.execute("""
        SELECT n.*, u.full_name AS author FROM news n
        LEFT JOIN users u ON u.id = n.author_id
        WHERE n.slug = ? AND n.published = 1
    """, (slug,)).fetchone()
    if not n:
        return api_error("новость не найдена", 404)
    return api_json({
        "ok": True,
        "item": {
            "id": n["id"], "title": n["title"], "slug": n["slug"],
            "summary": n["summary"] or "", "body": n["body"],
            "date": n["created_at"], "author": n["author"] or "",
        },
    })


@app.route("/api/v1/schedule")
def api_schedule():
    db = get_db()
    nowstr = datetime.now().strftime("%Y-%m-%dT%H:%M")
    up = db.execute("""
        SELECT e.*, u.full_name AS coach FROM events e
        LEFT JOIN users u ON u.id = e.coach_id
        WHERE e.starts_at >= ? ORDER BY e.starts_at ASC
    """, (nowstr,)).fetchall()
    past = db.execute("""
        SELECT e.*, u.full_name AS coach FROM events e
        LEFT JOIN users u ON u.id = e.coach_id
        WHERE e.starts_at < ? ORDER BY e.starts_at DESC LIMIT 20
    """, (nowstr,)).fetchall()

    def shape(rows):
        return [{
            "id": e["id"], "title": e["title"],
            "starts_at": e["starts_at"], "ends_at": e["ends_at"] or "",
            "place": e["place"] or "", "group": e["group_name"] or "",
            "coach": e["coach"] or "", "note": e["note"] or "",
        } for e in rows]

    return api_json({"ok": True, "upcoming": shape(up), "past": shape(past)})


@app.route("/api/v1/team")
def api_team():
    db = get_db()
    rows = db.execute("""
        SELECT DISTINCT u.* FROM users u
        JOIN user_roles r ON r.user_id = u.id
        WHERE r.role IN ('coach','athlete') AND u.is_approved = 1
        ORDER BY u.full_name
    """).fetchall()
    out = []
    for r in rows:
        p = api_person(r)
        p["awards_count"] = db.execute(
            "SELECT COUNT(*) c FROM awards WHERE user_id = ?",
            (r["id"],)).fetchone()["c"]
        p["aircraft_count"] = db.execute(
            "SELECT COUNT(*) c FROM aircraft WHERE user_id = ?",
            (r["id"],)).fetchone()["c"]
        out.append(p)
    return api_json({"ok": True, "count": len(out), "items": out})


@app.route("/api/v1/people/<int:uid>")
def api_person_one(uid):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row or not row["is_approved"]:
        return api_error("участник не найден", 404)

    # парк виден только владельцу и тренерам/админам
    viewer = api_token_user()
    show_fleet = (viewer and (viewer["id"] == uid
                              or has_role(viewer, "admin", "coach")))
    p = api_person(row, with_fleet=bool(show_fleet))
    awards = db.execute(
        "SELECT * FROM awards WHERE user_id = ? ORDER BY year DESC, place ASC",
        (uid,)).fetchall()
    p["awards"] = [{
        "id": a["id"], "title": a["title"],
        "event": a["event_name"] or "", "place": a["place"],
        "year": a["year"],
    } for a in awards]
    return api_json({"ok": True, "person": p})


# ── токены для приложения ──
import hashlib
import secrets


def ensure_tokens_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS api_tokens (
        token_hash TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        last_used  TEXT
    )""")
    db.commit()


def new_token(db, user_id):
    ensure_tokens_table(db)
    raw = secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    db.execute("""INSERT INTO api_tokens (token_hash, user_id, created_at)
                  VALUES (?, ?, ?)""",
               (h, user_id, datetime.now().isoformat(timespec="seconds")))
    db.commit()
    return raw


def api_token_user():
    """Пользователь по заголовку Authorization: Bearer <token>."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    raw = header[7:].strip()
    if not raw:
        return None
    h = hashlib.sha256(raw.encode()).hexdigest()
    db = get_db()
    ensure_tokens_table(db)
    row = db.execute("""
        SELECT u.* FROM api_tokens t JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = ? AND u.is_approved = 1
    """, (h,)).fetchone()
    if not row:
        return None
    db.execute("UPDATE api_tokens SET last_used = ? WHERE token_hash = ?",
               (datetime.now().isoformat(timespec="seconds"), h))
    db.commit()
    user = dict(row)
    user["roles"] = roles_of(user["id"])
    return user


@app.route("/api/v1/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or request.form
    ident = (data.get("ident") or "").strip()
    pwd = data.get("password") or ""
    if not ident or not pwd:
        return api_error("нужны ident и password", 400)

    db = get_db()
    row = db.execute("SELECT * FROM users WHERE email = ? OR phone = ?",
                     (ident, ident)).fetchone()
    if not row or not check_password_hash(row["password_hash"], pwd):
        return api_error("неверный логин или пароль", 401)
    if not row["is_approved"]:
        return api_error("заявка ещё не одобрена", 403)

    token = new_token(db, row["id"])
    return api_json({
        "ok": True,
        "token": token,
        "person": api_person(row),
    })


@app.route("/api/v1/me")
def api_me():
    user = api_token_user()
    if not user:
        return api_error("нужен токен", 401)
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    p = api_person(row, with_fleet=True)
    p["email"] = row["email"] or ""
    p["phone"] = row["phone"] or ""
    p["awards"] = [{
        "id": a["id"], "title": a["title"], "event": a["event_name"] or "",
        "place": a["place"], "year": a["year"],
    } for a in db.execute(
        "SELECT * FROM awards WHERE user_id = ? ORDER BY year DESC",
        (user["id"],)).fetchall()]
    return api_json({"ok": True, "person": p})


@app.route("/api/v1/me/fleet")
def api_me_fleet():
    user = api_token_user()
    if not user:
        return api_error("нужен токен", 401)
    db = get_db()
    fleet = db.execute(
        "SELECT * FROM aircraft WHERE user_id = ? ORDER BY class_code",
        (user["id"],)).fetchall()
    return api_json({
        "ok": True,
        "count": len(fleet),
        "counts": {s: sum(1 for a in fleet if a["status"] == s)
                   for s in STATUSES},
        "items": [{"id": a["id"], "name": a["name"],
                   "class": a["class_code"], "status": a["status"]}
                  for a in fleet],
    })


@app.route("/api/v1/me/fleet/<int:aid>", methods=["PATCH", "POST"])
def api_fleet_update(aid):
    user = api_token_user()
    if not user:
        return api_error("нужен токен", 401)
    data = request.get_json(silent=True) or request.form
    st = data.get("status")
    if st not in STATUSES:
        return api_error("неизвестный статус", 400)
    db = get_db()
    cur = db.execute(
        "UPDATE aircraft SET status = ? WHERE id = ? AND user_id = ?",
        (st, aid, user["id"]))
    db.commit()
    if cur.rowcount == 0:
        return api_error("модель не найдена", 404)
    return api_json({"ok": True, "id": aid, "status": st})


# ─────────────────────────────────────────────────────────── загрузки

@app.route("/uploads/<path:sub>")
def uploaded_file(sub):
    # защита от выхода за пределы папки
    if ".." in sub.split("/") or sub.startswith("/"):
        abort(404)
    return send_from_directory(UPLOAD_DIR, sub, max_age=60 * 60 * 24 * 30)


@app.route("/cabinet/photo", methods=["POST"])
@login_required
def upload_own_photo():
    user = current_user()
    rel, err = save_image(request.files.get("photo"), "people", square=480)
    if err:
        flash("Фото: " + err, "err")
        return redirect(url_for("cabinet"))

    db = get_db()
    old = db.execute("SELECT photo FROM users WHERE id = ?",
                     (user["id"],)).fetchone()["photo"]
    db.execute("UPDATE users SET photo = ? WHERE id = ?", (rel, user["id"]))
    db.commit()
    if old and old != rel:
        delete_image(old)
    flash("Фото обновлено", "ok")
    return redirect(url_for("cabinet"))


@app.route("/cabinet/photo/delete", methods=["POST"])
@login_required
def delete_own_photo():
    user = current_user()
    db = get_db()
    old = db.execute("SELECT photo FROM users WHERE id = ?",
                     (user["id"],)).fetchone()["photo"]
    db.execute("UPDATE users SET photo = NULL WHERE id = ?", (user["id"],))
    db.commit()
    delete_image(old)
    flash("Фото удалено", "ok")
    return redirect(url_for("cabinet"))


@app.route("/admin/users/<int:uid>/reset-password", methods=["POST"])
@role_required("admin")
def admin_reset_password(uid):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        abort(404)

    # временный пароль: легко продиктовать по телефону
    words = ["аэро", "крыло", "винт", "мотор", "фюзеляж", "планер",
             "элерон", "стабилизатор"]
    temp = "%s-%04d" % (secrets.choice(words), secrets.randbelow(9000) + 1000)

    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (generate_password_hash(temp), uid))
    db.commit()

    flash("Временный пароль для %s: %s — передайте лично и попросите "
          "сменить при входе" % (row["full_name"], temp), "warn")
    return redirect(url_for("admin"))


@app.route("/admin/backup")
@role_required("admin")
def admin_backup():
    """Скачивание базы. Только для админа — там хеши паролей."""
    if not os.path.exists(DB_PATH):
        abort(404)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    return send_file(
        DB_PATH,
        as_attachment=True,
        download_name="aero_%s.db" % stamp,
        mimetype="application/x-sqlite3",
    )


# ─── правка чужого профиля (только админ) ───────────────────────────────────

@app.route("/admin/users/<int:uid>/edit", methods=["GET", "POST"])
@role_required("admin")
def admin_user_edit(uid):
    """Заполнить за человека его профиль и парк моделей.

    Нужно тем, кто не может сам: пожилым, детям, у кого нет под рукой
    интернета. Админ видит те же поля, что в личном кабинете, но для
    выбранного пользователя. Роли и пароль здесь не меняются — для них
    отдельные кнопки в «Админе».
    """
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        abort(404)

    if request.method == "POST":
        f = request.form
        action = (f.get("action") or "profile").strip()

        if action == "profile":
            db.execute("""
                UPDATE users SET full_name = ?, title = ?, bio = ?,
                                 birth_date = ?
                WHERE id = ?
            """, ((f.get("full_name") or "").strip() or row["full_name"],
                  (f.get("title") or "").strip(),
                  (f.get("bio") or "").strip(),
                  (f.get("birth_date") or "").strip() or None,
                  uid))
            db.commit()
            flash("Профиль %s сохранён" % row["full_name"], "ok")

        elif action == "add_aircraft":
            name = (f.get("name") or "").strip()
            cls = (f.get("class_code") or "").strip().upper()
            st = f.get("status") or "fly"
            if name and cls:
                db.execute("""
                    INSERT INTO aircraft (user_id, name, class_code, status, note)
                    VALUES (?, ?, ?, ?, ?)
                """, (uid, name, cls, st if st in STATUSES else "fly",
                      (f.get("note") or "").strip()))
                db.commit()
                flash("Модель добавлена", "ok")

        elif action == "del_aircraft":
            aid = f.get("aid") or ""
            if aid.isdigit():
                db.execute("DELETE FROM aircraft WHERE id = ? AND user_id = ?",
                           (int(aid), uid))
                db.commit()
                flash("Модель удалена", "ok")

        elif action == "status":
            aid = f.get("aid") or ""
            st = f.get("status")
            if aid.isdigit() and st in STATUSES:
                db.execute("UPDATE aircraft SET status = ? "
                           "WHERE id = ? AND user_id = ?",
                           (st, int(aid), uid))
                db.commit()

        return redirect(url_for("admin_user_edit", uid=uid))

    person = dict(row)
    person["roles"] = roles_of(uid)
    fleet = db.execute(
        "SELECT * FROM aircraft WHERE user_id = ? ORDER BY class_code",
        (uid,)).fetchall()
    awards = db.execute(
        "SELECT * FROM awards WHERE user_id = ? ORDER BY year DESC, place ASC",
        (uid,)).fetchall()
    return render_template("admin_user_edit.html", person=person,
                           fleet=fleet, awards=awards)


@app.route("/admin/users/<int:uid>/photo", methods=["POST"])
@role_required("admin")
def admin_set_photo(uid):
    db = get_db()
    row = db.execute("SELECT photo FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        abort(404)
    rel, err = save_image(request.files.get("photo"), "people", square=480)
    if err:
        flash("Фото: " + err, "err")
        return redirect(url_for("admin"))

    db.execute("UPDATE users SET photo = ? WHERE id = ?", (rel, uid))
    db.commit()
    if row["photo"]:
        delete_image(row["photo"])
    flash("Фото обновлено", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:uid>/photo/delete", methods=["POST"])
@role_required("admin")
def admin_del_photo(uid):
    """Убрать чужое фото — например, если загрузили не то."""
    db = get_db()
    row = db.execute("SELECT photo FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        abort(404)
    db.execute("UPDATE users SET photo = NULL WHERE id = ?", (uid,))
    db.commit()
    if row["photo"]:
        delete_image(row["photo"])
    flash("Фото удалено", "ok")
    return redirect(request.form.get("back") or url_for("admin"))


# ─────────────────────────────────────────────────────────── PWA

@app.route("/manifest.webmanifest")
def manifest():
    """Манифест собирается на ходу: название берётся из настроек,
    поэтому имя приложения на телефоне меняется вместе с клубом."""
    cfg = get_settings()
    name = cfg["club_name"] or CLUB_NAME_DEFAULT
    short = cfg["club_short"] or CLUB_SHORT_DEFAULT
    city = cfg["club_city"] or CLUB_CITY_DEFAULT

    data = {
        "name": name,
        "short_name": short,
        "description": "Авиамодельный клуб %s: новости, расписание, команда"
                       % (city if city else ""),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#fbfaf8",
        "theme_color": "#1a1a1a",
        "lang": "ru",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192",
             "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-maskable.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Расписание", "url": "/schedule"},
            {"name": "Новости", "url": "/news"},
            {"name": "Кабинет", "url": "/cabinet"},
        ],
    }
    resp = app.response_class(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/sw.js")
def service_worker():
    """Service worker должен отдаваться с корня, чтобы управлять всем сайтом."""
    resp = app.send_static_file("sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/privacy")
def privacy():
    return render_template("privacy.html",
                           meta=meta_for(
                               title="Политика обработки ПД — %s" % get_settings()["club_name"],
                               description="Как клуб обрабатывает персональные данные."))


@app.route("/terms")
def terms():
    return render_template("terms.html",
                           meta=meta_for(
                               title="Соглашение — %s" % get_settings()["club_name"],
                               description="Условия использования сайта авиамодельного клуба."))


# ─── поисковики ──────────────────────────────────────────────────────────────

@app.route("/sitemap.xml")
def sitemap():
    """Карта сайта. Только публичные страницы: кабинет, админка и редактор
    закрыты от индексации."""
    db = get_db()
    urls = [{"loc": abs_url(p), "priority": pr, "freq": fr, "lastmod": None}
            for p, pr, fr in (
                ("/", "1.0", "daily"),
                ("/news", "0.9", "daily"),
                ("/schedule", "0.8", "weekly"),
                ("/team", "0.8", "weekly"),
                ("/about", "0.9", "monthly"),
                ("/privacy", "0.2", "yearly"),
                ("/terms", "0.2", "yearly"),
            )]

    for row in db.execute("""
        SELECT slug, created_at FROM news
        WHERE published = 1 ORDER BY created_at DESC LIMIT 500
    """):
        urls.append({"loc": abs_url("/news/" + row["slug"]),
                     "priority": "0.7", "freq": "monthly",
                     "lastmod": (row["created_at"] or "")[:10]})

    for row in db.execute("""
        SELECT u.id FROM users u
        JOIN user_roles r ON r.user_id = u.id
        WHERE r.role IN ('coach','athlete') AND u.is_approved = 1
        GROUP BY u.id
    """):
        urls.append({"loc": abs_url("/u/%d" % row["id"]),
                     "priority": "0.5", "freq": "monthly", "lastmod": None})

    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        body.append("  <url>")
        body.append("    <loc>%s</loc>" % escape(u["loc"]))
        if u["lastmod"]:
            body.append("    <lastmod>%s</lastmod>" % escape(u["lastmod"]))
        body.append("    <changefreq>%s</changefreq>" % u["freq"])
        body.append("    <priority>%s</priority>" % u["priority"])
        body.append("  </url>")
    body.append("</urlset>")

    resp = app.response_class("\n".join(body), mimetype="application/xml")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/robots.txt")
def robots():
    """Подсказки поисковикам: что индексировать и где карта сайта."""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /cabinet",
        "Disallow: /admin",
        "Disallow: /editor",
        "Disallow: /security",
        "Disallow: /login",
        "Disallow: /register",
        "Disallow: /logout",
        "Disallow: /api/",
        "Disallow: /consent/",
        "",
        "Sitemap: %s" % abs_url("/sitemap.xml"),
        "",
    ]
    resp = app.response_class("\n".join(lines), mimetype="text/plain")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


CONSENT_SIGNATURES = {"pdf": b"%PDF", "jpg": b"\xff\xd8\xff", "png": b"\x89PNG"}


def delete_private(rel):
    full = os.path.join(PRIVATE_DIR, *rel.split("/"))
    if os.path.isfile(full):
        os.remove(full)


@app.route("/admin/users/<int:uid>/consent", methods=["POST"])
@role_required("admin")
def admin_upload_consent(uid):
    db = get_db()
    row = db.execute("SELECT parent_consent FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        abort(404)
    f = request.files.get("consent")
    data = f.read(MAX_FILE + 1) if f else b""
    if not data:
        flash("Выберите файл", "err")
        return redirect(url_for("admin"))
    if len(data) > MAX_FILE:
        flash("Файл больше 8 МБ", "err")
        return redirect(url_for("admin"))
    ext = next((e for e, sig in CONSENT_SIGNATURES.items() if data.startswith(sig)), None)
    if not ext:
        flash("Допустимы только файлы PDF, JPG или PNG", "err")
        return redirect(url_for("admin"))
    os.makedirs(os.path.join(PRIVATE_DIR, "consent"), exist_ok=True)
    rel = "consent/%d_%s.%s" % (uid, secrets.token_hex(8), ext)
    with open(os.path.join(PRIVATE_DIR, *rel.split("/")), "wb") as out:
        out.write(data)
    db.execute("UPDATE users SET parent_consent = ? WHERE id = ?", (rel, uid))
    db.commit()
    if row["parent_consent"]:
        delete_private(row["parent_consent"])
    flash("Согласие родителя загружено", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/users/<int:uid>/consent/delete", methods=["POST"])
@role_required("admin")
def admin_delete_consent(uid):
    db = get_db()
    row = db.execute("SELECT parent_consent FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        abort(404)
    if row["parent_consent"]:
        delete_private(row["parent_consent"])
        db.execute("UPDATE users SET parent_consent = NULL WHERE id = ?", (uid,))
        db.commit()
    flash("Согласие удалено", "ok")
    return redirect(url_for("admin"))


@app.route("/consent/<int:uid>")
def view_consent(uid):
    if not has_role(current_user(), "admin", "coach"):
        abort(403)
    row = get_db().execute("SELECT parent_consent FROM users WHERE id = ?", (uid,)).fetchone()
    if not row or not row["parent_consent"]:
        abort(404)
    resp = send_from_directory(PRIVATE_DIR, row["parent_consent"])
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/offline")
def offline():
    return render_template("offline.html")


# ─────────────────────────────────────────────────────────── ошибки

@app.errorhandler(403)
def e403(e):
    return render_template("error.html", code=403,
                           msg="Недостаточно прав"), 403


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", code=404,
                           msg="Страница не найдена"), 404


if __name__ == "__main__":
    # Только для локальной разработки. На сервере запускается через
    # gunicorn (см. deploy/aero.service) — там отладчик выключен.
    debug = os.environ.get("AERO_DEBUG") == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug)
