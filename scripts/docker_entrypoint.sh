#!/bin/sh
# Entrypoint контейнера web (вызывается из docker-compose.yml).
#
# FIX: раньше вся цепочка «wait_for_db && migrate && collectstatic» была
# записана прямо в command внутри compose. Из-за volume .:/app код проекта
# на сервере берётся с хоста, и если в INSTALLED_APPS не было приложения
# «core», Django падал с «Unknown command: 'wait_for_db'», а restart:
# unless-stopped бесконечно перезапускал lms_web (статус restarting,
# деплой не дожидался healthy, check-domain падал с «Container is
# restarting»). Теперь скрипт лежит в scripts/ (монтируется volume'ом —
# обновляется без пересборки образа) и печатает внятные diagnostics.

set -e

# FIX: working directory может отличаться (dev-запуск вне контейнера) —
# переходим в корень проекта по расположению самого скрипта.
cd "$(dirname "$0")/.."

echo "[entrypoint] Проверка окружения Django..."

if ! python manage.py help 2>/dev/null | grep -Eq "^[[:space:]]+wait_for_db([[:space:]]|$)"; then
    echo "[FATAL] Команда 'wait_for_db' не найдена среди команд manage.py."
    echo "        Возможные причины:"
    echo "        1) В core/settings.py в INSTALLED_APPS отсутствует 'core'"
    echo "           (исправлено: добавлено — обновите код на сервере)."
    echo "        2) Файл core/management/commands/wait_for_db.py отсутствует"
    echo "           (проверьте git pull / права на чтение)."
    echo "        3) DJANGO_SETTINGS_MODULE указывает не на core.settings."
    exit 1
fi

echo "[entrypoint] Ожидание доступности базы данных..."
python manage.py wait_for_db

echo "[entrypoint] Применение миграций..."
python manage.py migrate --noinput

echo "[entrypoint] Сбор статики..."
python manage.py collectstatic --noinput

echo "[entrypoint] Готово, запускаем gunicorn."
