#!/usr/bin/env python3
# Интерактивная инициализация .env при первом деплое.
# Генерирует случайные ключи, запрашивает у пользователя важные значения.
#
# Запуск:
#     python /app/scripts/init_env.py

import secrets
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
ENV_EXAMPLE = BASE_DIR / ".env.example"

# Значения-заглушки, которые надо считать "незаполненными"
PLACEHOLDER_VALUES = {
    "",
    "change-me-generate-a-new-random-secret-key",
    "your-secret-key-here-change-in-production",
    "your-domain.example",
    "your-email@gmail.com",
    "your-app-password",
    "admin@your-domain.example",
    "CHANGE_ME",
}

# Ключи, которые МОЖНО сгенерировать автоматически
AUTO_GENERATE_KEYS = {
    "DJANGO_SECRET_KEY": lambda: secrets.token_urlsafe(64),
}

# Ключи, которые НЕЛЬЗЯ сгенерировать — нужно спросить у пользователя
# Формат: 'KEY': ('подсказка', 'значение_по_умолчанию')
USER_INPUT_KEYS = {
    "DOMAIN_NAME": ("Домен сайта", "study.lyagushkas.ru"),
    "EMAIL_HOST_USER": ("Email для отправки писем (Enter — пропустить)", ""),
    "EMAIL_HOST_PASSWORD": ("Пароль от email (Enter — пропустить)", ""),
    "CADDY_EMAIL": ("Email для SSL-сертификатов Caddy", "admin@lyagushkas.ru"),
}

# FIX: параметры PostgreSQL для режима прода. Спрашиваем только если
# пользователь согласился использовать PostgreSQL (или DB_ENGINE уже postgres).
POSTGRES_DEFAULTS = {
    "DB_NAME": ("Имя базы PostgreSQL", "lms_db"),
    "DB_USER": ("Пользователь PostgreSQL", "lms_user"),
    # DB_PASSWORD — генерируем надёжный пароль автоматически
}


def _ask_database(interactive):
    """Спрашивает, использовать ли PostgreSQL; при согласии возвращает блок БД для .env."""
    if interactive:
        answer = input("\n[i] Использовать PostgreSQL вместо SQLite в проде? (y/N): ").strip().lower()
        use_pg = answer in ("y", "yes", "да")
    else:
        use_pg = False
    if not use_pg:
        return {}
    changes = {"DB_ENGINE": "django.db.backends.postgresql"}
    for key, (hint, default) in POSTGRES_DEFAULTS.items():
        if interactive:
            value = input(f"   {hint} [{default}]: ").strip()
            changes[key] = value or default
        else:
            changes[key] = default
    changes["DB_PASSWORD"] = secrets.token_urlsafe(24)
    print(f"   [+] DB_PASSWORD = {changes['DB_PASSWORD'][:8]}... (сгенерирован)")
    # Внутри docker compose хост базы всегда «db» (compose сам выставит DB_HOST=db)
    changes["DB_HOST"] = "db"
    changes["DB_PORT"] = "5432"
    return changes


def parse_env(filepath):
    # Парсит .env файл в словарь {KEY: VALUE}, сохраняя порядок строк
    result = {}
    if not filepath.exists():
        return result
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, value = stripped.split("=", 1)
                result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def save_env(filepath, data):
    # Сохраняет словарь в .env файл (названия ключей сохраняем)
    # FIX: секреты в .env не должны оставаться читаемыми для всех пользователей.
    temporary_file = filepath.with_suffix(".tmp")
    with open(temporary_file, "w", encoding="utf-8") as f:
        for key, value in data.items():
            f.write(f"{key}={value}\n")
    temporary_file.replace(filepath)
    try:
        filepath.chmod(0o600)
    except OSError as exc:
        print(f"[WARN] Не удалось ограничить права {filepath}: {exc}")


def is_empty(value):
    normalized = (value or "").strip()
    return (
        not normalized
        or normalized in PLACEHOLDER_VALUES
        or normalized.startswith(("django-insecure-", "fallback-", "your-"))
    )


def main():
    print("Инициализация .env файла\n")

    # Если .env нет — копируем из .env.example
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
            print(f"[OK] Скопирован {ENV_EXAMPLE.name} -> .env\n")
        else:
            print("[!] .env.example не найден. Создаём пустой .env")
            ENV_FILE.touch()
    else:
        print("[i] .env уже существует — проверяем заполненность\n")

    env_data = parse_env(ENV_FILE)
    changed = False
    interactive = sys.stdin.isatty()

    # 1. Автогенерация ключей
    print("[i] Автоматическая генерация ключей:")
    for key, generator in AUTO_GENERATE_KEYS.items():
        current = env_data.get(key, "")
        if is_empty(current):
            new_value = generator()
            env_data[key] = new_value
            print(f"   [+] {key} = {new_value[:20]}... (сгенерирован)")
            changed = True
        else:
            print(f"   [=] {key} — уже задан, пропускаем")

    # FIX: 1.5 Выбор движка базы данных (SQLite / PostgreSQL).
    # Раньше _ask_database была определена, но никогда не вызывалась —
    # параметры БД не попадали в .env и прод-запуск с postgres падал
    # из-за отсутствия DB_USER/DB_PASSWORD.
    existing_engine = env_data.get("DB_ENGINE", "")
    if not is_empty(existing_engine) and "postgresql" in existing_engine:
        print("\n[i] DB_ENGINE уже задан как PostgreSQL — пропускаем опрос, сверяем параметры:")
        missing = {k: d for k, (_, d) in POSTGRES_DEFAULTS.items() if is_empty(env_data.get(k, ""))}
        for key, default in missing.items():
            if interactive:
                value = input(f"   {POSTGRES_DEFAULTS[key][0]} [{default}]: ").strip()
                env_data[key] = value or default
            else:
                env_data[key] = default
            print(f"   [~] {key} = {env_data[key]}")
            changed = True
        if is_empty(env_data.get("DB_PASSWORD", "")):
            env_data["DB_PASSWORD"] = secrets.token_urlsafe(24)
            print(f"   [+] DB_PASSWORD = {env_data['DB_PASSWORD'][:8]}... (сгенерирован)")
            changed = True
        env_data.setdefault("DB_HOST", "db")
        env_data.setdefault("DB_PORT", "5432")
    else:
        db_changes = _ask_database(interactive)
        if db_changes:
            env_data.update(db_changes)
            changed = True

    # 2. Запрос у пользователя
    print("\n[i] Заполнение параметров (Enter = значение по умолчанию):")
    for key, (hint, default) in USER_INPUT_KEYS.items():
        current = env_data.get(key, "")
        if not is_empty(current):
            print(f"   [=] {key} = {current} (уже задан)")
            continue
        if not interactive:
            # Неинтерактивный режим (например, в CI) — пишем значение по умолчанию
            env_data[key] = default
            print(f"   [~] {key} = {default} (неинтерактивно)")
            changed = True
            continue
        prompt = f"   {hint}"
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        value = input(prompt).strip()
        env_data[key] = value if value else default
        changed = True

    # 3. Производные значения
    if env_data.get("DOMAIN_NAME"):
        domain = env_data["DOMAIN_NAME"]
        allowed = env_data.get("DJANGO_ALLOWED_HOSTS", "")
        if is_empty(allowed) or "your-domain" in allowed or allowed == "localhost,127.0.0.1":
            env_data["DJANGO_ALLOWED_HOSTS"] = f"{domain},localhost,127.0.0.1"
            changed = True
        csrf = env_data.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
        if is_empty(csrf) or "your-domain" in csrf:
            env_data["DJANGO_CSRF_TRUSTED_ORIGINS"] = f"https://{domain},http://localhost"
            changed = True
        if is_empty(env_data.get("SITE_URL", "")) or "your-domain" in env_data.get("SITE_URL", ""):
            env_data["SITE_URL"] = f"https://{domain}"
            changed = True

    # 4. Сохранение
    if changed:
        try:
            save_env(ENV_FILE, env_data)
        except OSError as exc:
            print(f"\n[ERROR] Не удалось сохранить .env: {exc}")
            return 1
        print(f"\n[OK] .env сохранён: {ENV_FILE}")
    else:
        print("\n[OK] .env уже полностью настроен, изменений не внесено")


if __name__ == "__main__":
    raise SystemExit(main() or 0)
