#!/usr/bin/env python3
# Скрипт проверки соответствия домена в .env и Caddyfile.
# Если домены отличаются — спрашивает пользователя о замене.
#
# Запуск:
#     python /app/scripts/check_domain.py            # интерактивно
#     python /app/scripts/check_domain.py --auto-yes # без вопросов (для make deploy)

import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env'
CADDYFILE = BASE_DIR / 'Caddyfile'

# Регулярка для поиска домена (первая строка-директива, не localhost)
DOMAIN_RE = r'^(?!http://localhost)([a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'


def get_env_domain():
    """Получить домен из .env"""
    if not ENV_FILE.exists():
        print(f"[ERROR] .env не найден: {ENV_FILE}")
        return None
    with open(ENV_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('DOMAIN_NAME='):
                value = line.split('=', 1)[1].strip()
                return value.strip('"').strip("'")
    return None


def get_caddy_domain():
    """Получить домен из Caddyfile"""
    if not CADDYFILE.exists():
        print(f"[ERROR] Caddyfile не найден: {CADDYFILE}")
        return None
    with open(CADDYFILE, 'r', encoding='utf-8') as f:
        content = f.read()

    match = re.search(DOMAIN_RE, content, re.MULTILINE)
    return match.group(1) if match else None


def update_caddyfile(new_domain):
    """Обновить домен в Caddyfile"""
    with open(CADDYFILE, 'r', encoding='utf-8') as f:
        content = f.read()

    content = re.sub(
        DOMAIN_RE,
        new_domain,
        content,
        count=1,
        flags=re.MULTILINE,
    )

    with open(CADDYFILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[OK] Caddyfile обновлён: домен изменён на {new_domain}")


def restart_caddy():
    """Перезапустить Caddy через docker-compose"""
    print("[..] Перезапуск Caddy...")
    # Пытаемся V2, при неудаче — старый docker-compose
    if os.system('docker compose restart caddy') != 0:
        os.system('docker-compose restart caddy')
    print("[OK] Caddy перезапущен")


def main():
    env_domain = get_env_domain()
    caddy_domain = get_caddy_domain()

    print(f"[i] Домен в .env:      {env_domain}")
    print(f"[i] Домен в Caddyfile: {caddy_domain}")

    if not env_domain or not caddy_domain:
        print("[ERROR] Не удалось прочитать домены")
        sys.exit(1)

    if env_domain == caddy_domain:
        print("[OK] Домены совпадают. Всё в порядке!")
        sys.exit(0)

    print("\n[!] ВНИМАНИЕ: Домены не совпадают!")
    print(f"    .env:      {env_domain}")
    print(f"    Caddyfile: {caddy_domain}")

    auto_yes = '--auto-yes' in sys.argv
    if auto_yes:
        response = 'y'
    else:
        response = input("\nЗаменить домен в Caddyfile на значение из .env? (y/n): ").strip().lower()

    if response in ('y', 'yes', 'да'):
        update_caddyfile(env_domain)
        restart_caddy()
        print("[OK] Готово! Домен обновлён и Caddy перезапущен.")
    else:
        print("[X] Отменено. Домен не изменён.")
        sys.exit(1)


if __name__ == '__main__':
    main()
