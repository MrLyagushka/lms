#!/usr/bin/env python3
"""
Скрипт проверки соответствия домена в .env и Caddyfile.
Если домены отличаются — спрашивает пользователя о замене.
"""

import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / '.env'
CADDYFILE = BASE_DIR / 'Caddyfile'


def get_env_domain():
    """Получить домен из .env"""
    if not ENV_FILE.exists():
        print("[ERROR] Файл .env не найден!")
        sys.exit(1)

    with open(ENV_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('DOMAIN_NAME='):
                return line.split('=', 1)[1].strip()
    return None


def get_caddy_domain():
    """Получить домен из Caddyfile"""
    if not CADDYFILE.exists():
        print("[ERROR] Файл Caddyfile не найден!")
        sys.exit(1)

    with open(CADDYFILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # Ищем первый домен (не localhost)
    match = re.search(
        r'^(?!http://localhost)([a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        content,
        re.MULTILINE,
    )
    if match:
        return match.group(1)
    return None


def update_caddyfile(new_domain):
    """Обновить домен в Caddyfile"""
    with open(CADDYFILE, 'r', encoding='utf-8') as f:
        content = f.read()

    content = re.sub(
        r'^(?!http://localhost)([a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
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
    os.system('docker-compose restart caddy')
    print("[OK] Caddy перезапущен")


def main():
    env_domain = get_env_domain()
    caddy_domain = get_caddy_domain()

    print(f"[i] Домен в .env:      {env_domain}")
    print(f"[i] Домен в Caddyfile: {caddy_domain}")

    if env_domain == caddy_domain:
        print("[OK] Домены совпадают. Всё в порядке!")
        sys.exit(0)

    print("\n[!] ВНИМАНИЕ: Домены не совпадают!")
    print(f"    .env:      {env_domain}")
    print(f"    Caddyfile: {caddy_domain}")

    if len(sys.argv) > 1 and sys.argv[1] == '--auto-yes':
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
