"""Команда «python manage.py wait_for_db» — ожидание доступности БД.

Используется в entrypoint контейнера web (docker-compose), чтобы migrate
не падал с «connection refused», если PostgreSQL ещё не поднялся
(или поднимается медленнее, чем web). Для SQLite проверка trivialльна —
файл базы создаётся автоматически.
"""

import time

from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Ждёт, пока база данных станет доступной (для запуска в docker compose)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--retries",
            type=int,
            default=30,
            help="Сколько раз повторять попытку подключения (по умолчанию 30).",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=2.0,
            help="Пауза между попытками в секундах (по умолчанию 2).",
        )

    def handle(self, *args, **options):
        retries = max(1, options["retries"])
        delay = max(0.1, options["delay"])
        alias = "default"

        for attempt in range(1, retries + 1):
            try:
                conn = connections[alias]
                conn.close()          # сбрасываем возможное «протухшее» соединение
                conn.ensure_connection()
                self.stdout.write(
                    self.style.SUCCESS(f"[OK] База данных '{alias}' доступна (попытка {attempt}).")
                )
                return
            except Exception as exc:  # noqa: BLE001 — ждём любую ошибку подключения
                if attempt == retries:
                    self.stderr.write(
                        self.style.ERROR(
                            f"[ERROR] База данных '{alias}' недоступна после {retries} "
                            f"попыток: {exc}"
                        )
                    )
                    # FIX: «could not translate host name "db"» означает, что
                    # контейнер PostgreSQL (сервис db) просто не запущен —
                    # профиль postgres был выключен при деплое. Даём прямую
                    # подсказку вместо абстрактной ошибки сети.
                    if "translate host name" in str(exc):
                        self.stderr.write(self.style.ERROR(
                            "[HINT] Хост БД не резолвится: сервис db не поднят.\n"
                            "       Обновите Makefile (git pull) — он теперь сам включает профиль по DB_ENGINE в .env; либо выполните: make deploy POSTGRES=1\n"
                            "       (Makefile также сам включает профиль, если в .env\n"
                            "       стоит DB_ENGINE=*postgresql*)."
                        ))
                    raise SystemExit(1) from exc
                self.stdout.write(
                    f"[..] Ожидание БД '{alias}' ({attempt}/{retries}): {exc.__class__.__name__}. "
                    f"Повтор через {delay:g} c…"
                )
                time.sleep(delay)
