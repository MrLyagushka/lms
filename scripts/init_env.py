#!/usr/bin/env python3
# Интерактивная инициализация .env при первом деплое.
# Генерирует случайные ключи, запрашивает у пользователя важные значения.
#
# Запуск:
#     python /app/scripts/init_env.py            # интерактивный режим
#     python /app/scripts/init_env.py --postgres # прод: PostgreSQL без вопросов
#     LMS_POSTGRES=1 DOMAIN_NAME=... CADDY_EMAIL=... \
#     EMAIL_HOST_USER=... EMAIL_HOST_PASSWORD=... \
#     python /app/scripts/init_env.py            # CI: всё из env-переменных
#
# Флаги:
#     --postgres / --prod : включить PostgreSQL (пароль генерируется)
#     --sqlite            : оставить SQLite без опроса
#     -y / --yes          : не задавать вопросов (пустые значения -> дефолты)

import os
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


def parse_args(argv):
    """Разбирает флаги и env-переменные. Возвращает dict с режимом работы."""
    opts = {"postgres": None, "yes": False}
    for arg in argv:
        if arg in ("--postgres", "--prod"):
            opts["postgres"] = True
        elif arg == "--sqlite":
            opts["postgres"] = False
        elif arg in ("-y", "--yes"):
            opts["yes"] = True
        elif arg.startswith("-"):
            print(f"[!] Неизвестный флаг: {arg} (см. --help в начале файла)")
    # Fallback на переменные окружения (docker compose run их не передаёт)
    if opts["postgres"] is None:
        flag = os.environ.get("LMS_POSTGRES", "").strip().lower()
        if flag in ("1", "true", "yes", "y", "on"):
            opts["postgres"] = True
        elif flag in ("0", "false", "no", "n", "off"):
            opts["postgres"] = False
    if not opts["yes"]:
        opts["yes"] = os.environ.get("LMS_YES", "").strip().lower() in ("1", "true", "yes")
    return opts


def _ask_database(interactive, force=None):
    """Спрашивает, использовать ли PostgreSQL; при согласии возвращает блок БД для .env.

    force=True/False — ответ задан флагом/env, вопрос не задаётся.
    """
    if force is True:
        use_pg = True
        if interactive:
            print("[i] PostgreSQL выбран (флаг/переменная), вопросы по параметрам не задаются")
    elif force is False:
        use_pg = False
    elif interactive:
        answer = input("\n[i] Использовать PostgreSQL вместо SQLite в проде? (y/N): ").strip().lower()
        use_pg = answer in ("y", "yes", "да")
    else:
        use_pg = False
    if not use_pg:
        return {}
    changes = {"DB_ENGINE": "django.db.backends.postgresql"}
    for key, (hint, default) in POSTGRES_DEFAULTS.items():
        env_val = os.environ.get(key, "").strip()
        if env_val:
            changes[key] = env_val
            print(f"   [~] {key} = {env_val} (из переменных окружения)")
        elif force is not None or not interactive:
            changes[key] = default  # неинтерактивно/по флагу — дефолты без вопросов
        else:
            value = input(f"   {hint} [{default}]: ").strip()
            changes[key] = value or default
    env_pass = os.environ.get("DB_PASSWORD", "").strip()
    if env_pass and env_pass != "change-me-strong-password":
        changes["DB_PASSWORD"] = env_pass
        print("   [~] DB_PASSWORD взят из переменных окружения")
    else:
        changes["DB_PASSWORD"] = secrets.token_urlsafe(24)
        print(f"   [+] DB_PASSWORD = {changes['DB_PASSWORD'][:8]}... (сгенерирован)")
    # Внутри docker compose хост базы всегда «db» (compose сам выставит DB_HOST=db)
    changes["DB_HOST"] = os.environ.get("DB_HOST", "").strip() or "db"
    changes["DB_PORT"] = os.environ.get("DB_PORT", "").strip() or "5432"
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


def _atomic_write(target, text):
    """Атомарная запись файла; при отказе temp-пути пишет напрямую в target."""
    temporary_file = target.with_name(target.name + ".tmp")
    try:
        with open(temporary_file, "w", encoding="utf-8") as f:
            f.write(text)
        temporary_file.replace(target)
    except OSError as exc:
        # FIX: если temp-файл создать нельзя (например, leftover-файл принадлежит
        # другому пользователю в смонтированном томе) — пишем напрямую в .env.
        print(f"[WARN] Атомарная запись не удалась ({exc}); пишу напрямую в {target.name}")
        try:
            temporary_file.unlink()
        except OSError:
            pass
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
        except PermissionError as pexc:
            # FIX: понятное сообщение вместо голого Errno 13: контейнер работает
            # от пользователя lms, а .env может принадлежать root на хосте.
            raise RuntimeError(
                f"Нет прав на запись в {target} ({pexc}). "
                "Исправьте на хосте: sudo chown $(id -u):$(id -g) .env && chmod 600 .env. "
                "Настройка .env больше не выполняется из контейнера — используйте ./install.sh."
            ) from pexc


def save_env(filepath, data):
    # Сохраняет словарь в .env файл (названия ключей сохраняем)
    # FIX: секреты в .env не должны оставаться читаемыми для всех пользователей.
    text = "".join(f"{key}={value}\n" for key, value in data.items())
    _atomic_write(filepath, text)
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


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
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
    interactive = sys.stdin.isatty() and not opts["yes"]

    # 0. FIX: значения из переменных окружения имеют приоритет (non-interactive
    # запуск в docker compose run и CI). Пустые строки игнорируются, чтобы
    # os.environ без ключа не затирал существующие значения.
    for key in list(USER_INPUT_KEYS) + ["DB_ENGINE", "SITE_URL", "DJANGO_ALLOWED_HOSTS"]:
        value = os.environ.get(key, "").strip()
        if value and env_data.get(key, "") != value:
            env_data[key] = value
            changed = True
            shown = value[:20] + ("..." if len(value) > 20 else "")
            print(f"[~] {key} = {shown} (из переменных окружения)")

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
    pg_forced = opts["postgres"] is True or (
        not is_empty(existing_engine) and "postgresql" in existing_engine
    )
    if pg_forced:
        # FIX: postgres-параметры должны попасть в .env даже если файла ещё нет
        # или движок был выбран флагом (--postgres), а не задан заранее.
        if "postgresql" in (existing_engine or ""):
            print("\n[i] DB_ENGINE уже задан как PostgreSQL — пропускаем опрос, сверяем параметры:")
        elif opts["postgres"] is True:
            env_data["DB_ENGINE"] = "django.db.backends.postgresql"
            changed = True
        missing = {k: d for k, (_, d) in POSTGRES_DEFAULTS.items() if is_empty(env_data.get(k, ""))}
        # FIX: при переходе на postgres имя базы из sqlite-шаблона (db.sqlite3)
        # не имеет смысла — просим/ставим нормальное имя базы.
        current_dbname = env_data.get("DB_NAME", "")
        if "postgresql" in (env_data.get("DB_ENGINE") or "") and current_dbname.strip() == "db.sqlite3":
            missing["DB_NAME"] = POSTGRES_DEFAULTS["DB_NAME"][1]
        for key, default in missing.items():
            env_val = os.environ.get(key, "").strip()
            if env_val:
                env_data[key] = env_val
            elif interactive and opts["postgres"] is None:
                value = input(f"   {POSTGRES_DEFAULTS[key][0]} [{default}]: ").strip()
                env_data[key] = value or default
            else:
                env_data[key] = default
            print(f"   [~] {key} = {env_data[key]}")
            changed = True
        if is_empty(env_data.get("DB_PASSWORD", "")):
            env_pass = os.environ.get("DB_PASSWORD", "").strip()
            if env_pass and env_pass != "change-me-strong-password":
                env_data["DB_PASSWORD"] = env_pass
            else:
                env_data["DB_PASSWORD"] = secrets.token_urlsafe(24)
                print(f"   [+] DB_PASSWORD = {env_data['DB_PASSWORD'][:8]}... (сгенерирован)")
            changed = True
        env_data.setdefault("DB_HOST", "db")
        env_data.setdefault("DB_PORT", "5432")
    elif opts["postgres"] is False:
        pass  # SQLite выбран явно (--sqlite) — не спрашиваем
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
        except (OSError, RuntimeError) as exc:
            print(f"\n[ERROR] Не удалось сохранить .env: {exc}")
            return 1
        print(f"\n[OK] .env сохранён: {ENV_FILE}")
    else:
        print("\n[OK] .env уже полностью настроен, изменений не внесено")


if __name__ == "__main__":
    raise SystemExit(main() or 0)
