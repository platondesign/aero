#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Выдаёт аккаунту все роли и одобряет его.

    python make_admin.py ваш@email.ru
    python make_admin.py +79000000000

Выдаёт все четыре роли и одобряет аккаунт.

Снять роли (оставить обычным участником):
    python make_admin.py --demote ваш@email.ru

Нужен, чтобы сделать первого администратора: до этого момента
раздавать роли некому. И чтобы вернуть себе обычный аккаунт,
когда появится отдельный админский.
"""

import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "aero.db")

ALL_ROLES = ("admin", "editor", "coach", "athlete")


DEMOTE = {"--demote", "-d", "--remove"}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    demote = bool(flags & DEMOTE)

    if not args:
        print(__doc__)
        return 1

    ident = args[0].strip()
    if not os.path.exists(DB_PATH):
        print("База не найдена: %s" % DB_PATH)
        print("Сначала запустите сайт один раз: python app.py")
        return 1

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    row = db.execute(
        "SELECT * FROM users WHERE email = ? OR phone = ?", (ident, ident)
    ).fetchone()

    if not row:
        print("Пользователь не найден: %s" % ident)
        print("\nЗарегистрируйтесь на сайте (/register) и повторите.")
        return 1

    if demote:
        # не даём остаться без единого администратора
        admins = db.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM user_roles WHERE role = 'admin'"
        ).fetchone()["c"]
        is_admin = db.execute(
            "SELECT 1 FROM user_roles WHERE user_id = ? AND role = 'admin'",
            (row["id"],)).fetchone()
        if is_admin and admins <= 1:
            print("Это единственный администратор — снять роль нельзя.")
            print("Сначала назначьте другого через веб-панель или этим скриптом.")
            return 1

        db.execute("DELETE FROM user_roles WHERE user_id = ?", (row["id"],))
        db.execute("INSERT INTO user_roles (user_id, role) VALUES (?, 'athlete')",
                   (row["id"],))
        db.commit()
        print("Роли сняты.")
        print("  пользователь : %s" % row["full_name"])
        print("  осталось     : Спортсмен (обычный участник)")
        return 0

    db.execute("UPDATE users SET is_approved = 1 WHERE id = ?", (row["id"],))
    db.execute("DELETE FROM user_roles WHERE user_id = ?", (row["id"],))
    for r in ALL_ROLES:
        db.execute("INSERT INTO user_roles (user_id, role) VALUES (?, ?)",
                   (row["id"], r))
    db.commit()

    print("Готово.")
    print("  пользователь : %s" % row["full_name"])
    print("  id           : %d" % row["id"])
    print("  роли         : %s" % ", ".join(ALL_ROLES))
    print("  одобрен      : да")
    print("\nВойдите на сайте — появятся разделы «Редактор» и «Админ».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
