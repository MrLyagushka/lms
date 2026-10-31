#!/usr/bin/env bash
# =============================================================================
#  install.sh — установка окружения проекта LMS (переменные + библиотеки)
#
#  Делает:
#    1. Проверяет/устанавливает системные зависимости (python3, pip, venv, git;
#       опционально docker + compose для режима прода).
#    2. Создаёт виртуальное окружение .venv и ставит библиотеки из requirements.txt.
#    3. Формирует .env из .env.example: генерирует DJANGO_SECRET_KEY, подставляет
#       домен и email; с флагом --postgres настраивает блок PostgreSQL
#       (генерирует надёжный пароль DB_PASSWORD).
#    4. Применяет миграции и собирает статику (режим dev — в локальном venv).
#
#  Запуск:
#    ./install.sh                 # dev-режим: venv + SQLite + миграции
#    ./install.sh --postgres      # прод-режим: проверка docker + .env с PostgreSQL
#    ./install.sh --no-venv       # ставить библиотеки в системный python
#    ./install.sh --skip-system   # не пытаться ставить системные пакеты
#    DOMAIN=example.com EMAIL=admin@example.com ./install.sh
#
#  Идемпотентен: повторный запуск ничего не ломает (.env не перезаписывается,
#  ключ регенерируется только если остался заглушкой).
# =============================================================================
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
ROOT="$(pwd)"

# --------------------------- параметры --------------------------------------
USE_POSTGRES=0
USE_VENV=1
SKIP_SYSTEM=0
WITH_DOCKER_CHECK=0

for arg in "$@"; do
    case "$arg" in
        --postgres)      USE_POSTGRES=1; WITH_DOCKER_CHECK=1 ;;
        --no-venv)       USE_VENV=0 ;;
        --skip-system)   SKIP_SYSTEM=1 ;;
        -h|--help)       grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "[!] Неизвестный аргумент: $arg (см. --help)"; exit 1 ;;
    esac
done

DOMAIN="${DOMAIN:-study.lyagushkas.ru}"
CADDY_EMAIL="${EMAIL:-admin@${DOMAIN}}"

info() { echo -e "\033[36m[i]\033[0m $*"; }
ok()   { echo -e "\033[32m[OK]\033[0m $*"; }
warn() { echo -e "\033[33m[!]\033[0m $*"; }
die()  { echo -e "\033[31m[x]\033[0m $*" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
        warn "Не root и sudo нет — системная установка будет пропущена."
        SKIP_SYSTEM=1
    fi
fi

# --------------------- 1. системные зависимости ------------------------------
info "Шаг 1/4: проверка системных зависимостей"

MISSING=()
command -v python3  >/dev/null 2>&1 || MISSING+=(python3)
command -v git      >/dev/null 2>&1 || MISSING+=(git)

if [ "$USE_VENV" -eq 1 ] && ! python3 -c "import venv" >/dev/null 2>&1; then
    MISSING+=(python3-venv)
fi

if [ "$WITH_DOCKER_CHECK" -eq 1 ]; then
    # Проверка docker только если он есть (или разрешена системная установка).
    if command -v docker >/dev/null 2>&1; then
        if ! docker compose version >/dev/null 2>&1; then
            MISSING+=("docker-compose-plugin")
        fi
    elif [ "$SKIP_SYSTEM" -eq 0 ]; then
        MISSING+=(docker.io)
    else
        die "Docker не найден, а установка пропущена (--skip-system). Режим --postgres требует Docker для compose-сервиса db."
    fi
fi

if [ "${#MISSING[@]}" -gt 0 ]; then
    info "Отсутствуют: ${MISSING[*]}"
    if [ "$SKIP_SYSTEM" -eq 1 ]; then
        die "Установка пропущена (--skip-system). Установите вручную: apt install ${MISSING[*]}"
    fi
    if command -v apt-get >/dev/null 2>&1; then
        PKGS=()
        for p in "${MISSING[@]}"; do
            case "$p" in
                python3-venv)          PKGS+=(python3-venv) ;;
                docker-compose-plugin) PKGS+=(docker-compose-v2) ;;
                docker.io)             PKGS+=(docker.io) ;;
                *)                     PKGS+=("$p") ;;
            esac
        done
        info "Устанавливаю через apt-get: ${PKGS[*]}"
        $SUDO apt-get update -y
        DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "${PKGS[@]}"
        ok "Системные пакеты установлены."
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y python3 python3-pip git which $( [ "$WITH_DOCKER_CHECK" -eq 1 ] && echo "docker-ce docker-ce-cli containerd.io" )
        ok "Системные пакеты установлены (dnf)."
    else
        die "Не найден пакетный менеджер (apt/dnf). Установите вручную: ${MISSING[*]}"
    fi
else
    ok "Системные зависимости на месте."
fi

# Проверка pip (ставится вместе с python3-pip при необходимости)
PIP_CMD=(python3 -m pip)
if ! python3 -m pip --version >/dev/null 2>&1; then
    info "Устанавливаю pip..."
    if command -v apt-get >/dev/null 2>&1 && [ "$SKIP_SYSTEM" -eq 0 ]; then
        $SUDO apt-get install -y python3-pip
    else
        python3 -m ensurepip --upgrade
    fi
fi
[ -f requirements.txt ] || die "requirements.txt не найден в $ROOT"
ok "Python: $(python3 --version), pip: $(python3 -m pip --version | cut -d' ' -f1-2)"

# --------------------- 2. виртуальное окружение ------------------------------
info "Шаг 2/4: python-библиотеки (requirements.txt)"

PY_BIN="python3"
if [ "$USE_VENV" -eq 1 ]; then
    VENV_DIR="$ROOT/.venv"
    if [ ! -d "$VENV_DIR" ]; then
        info "Создаю виртуальное окружение $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    PY_BIN="$VENV_DIR/bin/python"
    ok "venv активирован ($($PY_BIN --version))."
else
    warn "venv пропущен (--no-venv): библиотеки ставятся в системный python."
fi

"$PY_BIN" -m pip install --upgrade pip >/dev/null
"$PY_BIN" -m pip install -r requirements.txt
ok "Библиотеки установлены: $($PY_BIN -m pip freeze | wc -l) пакетов."

# --------------------- 3. переменные окружения (.env) ------------------------
info "Шаг 3/4: настройка .env"

[ -f .env.example ] || die ".env.example не найден — невозможно создать .env"

if [ -f .env ]; then
    info ".env уже существует — обновляю только недостающие/заглушечные значения."
else
    cp .env.example .env
    ok ".env создан из .env.example"
fi

gen_secret() { "$PY_BIN" - <<'EOF'
import secrets
print(secrets.token_urlsafe(64))
EOF
}

gen_password() { "$PY_BIN" - <<'EOF'
import secrets, string
alphabet = string.ascii_letters + string.digits
print(''.join(secrets.choice(alphabet) for _ in range(24)))
EOF
}

set_env_var() {  # set_env_var KEY VALUE — заменить или дописать строку в .env
    local key="$1" val="$2"
    if grep -qE "^${key}=" .env; then
        "$PY_BIN" - "$key" "$val" <<'EOF'
import sys, re
key, val = sys.argv[1], sys.argv[2]
p = ".env"
text = open(p, encoding="utf-8").read()
text = re.sub(rf"^{key}=.*$", f"{key}={val}", text, flags=re.M)
open(p, "w", encoding="utf-8").write(text)
EOF
    else
        echo "${key}=${val}" >> .env
    fi
}

get_env_var() {
    grep -E "^$1=" .env | head -n1 | cut -d= -f2- || true
}

# SECRET_KEY — генерируем, если пустой или остался заглушкой
CUR_SECRET="$(get_env_var DJANGO_SECRET_KEY)"
case "$CUR_SECRET" in
    ""|change-me*|your-secret-key*)
        set_env_var DJANGO_SECRET_KEY "$(gen_secret)"
        ok "DJANGO_SECRET_KEY сгенерирован." ;;
    *) info "DJANGO_SECRET_KEY уже задан — оставляю как есть." ;;
esac

# Домен / URL / Caddy
set_env_var DOMAIN_NAME "$DOMAIN"
set_env_var SITE_URL "https://${DOMAIN}"
set_env_var CADDY_EMAIL "$CADDY_EMAIL"
ok "Домен: $DOMAIN, email Caddy: $CADDY_EMAIL"

if [ "$USE_POSTGRES" -eq 1 ]; then
    info "Режим PostgreSQL: настраиваю блок БД в .env"

    # Сначала раскомментируем закомментированный postgres-блок из шаблона,
    # затем нормализуем .env (убираем дубликаты ключей — later wins).
    "$PY_BIN" - <<'EOF'
import re
text = open(".env", encoding="utf-8").read()
keys = ("DB_ENGINE","DB_NAME","DB_USER","DB_PASSWORD","DB_HOST","DB_PORT",
        "DB_CONN_MAX_AGE","DB_CONNECT_TIMEOUT")
pattern = r"^#\s*((?:%s)=.*)$" % "|".join(keys)
new = re.sub(pattern, r"\1", text, flags=re.M)
lines, seen = [], set()
for line in new.splitlines():
    m = re.match(r"^([A-Z0-9_]+)=", line)
    if m:
        if m.group(1) in seen:
            continue  # убираем дубликат, оставляем первое вхождение
        seen.add(m.group(1))
    lines.append(line)
open(".env", "w", encoding="utf-8").write("\n".join(lines) + "\n")
EOF

    CUR_ENGINE="$(get_env_var DB_ENGINE)"
    if [ "$CUR_ENGINE" != "django.db.backends.postgresql" ]; then
        set_env_var DB_ENGINE "django.db.backends.postgresql"
        # SQLite-значение имени базы заменяем на postgres-дефолт
        case "$(get_env_var DB_NAME)" in
            ""|db.sqlite3) set_env_var DB_NAME "lms_db" ;;
        esac
        [ -z "$(get_env_var DB_USER)" ] && set_env_var DB_USER "lms_user"
        CUR_PGPASS="$(get_env_var DB_PASSWORD)"
        case "$CUR_PGPASS" in
            ""|change-me*) set_env_var DB_PASSWORD "$(gen_password)"
                           ok "DB_PASSWORD сгенерирован (сохраните его из .env)." ;;
            *) info "DB_PASSWORD уже задан — оставляю." ;;
        esac
        set_env_var DB_HOST "db"
        set_env_var DB_PORT "5432"
        set_env_var DB_CONN_MAX_AGE "60"
        set_env_var DB_CONNECT_TIMEOUT "10"
    else
        # Движок уже postgres: подчищаем только заглушки/пустые значения.
        CUR_PGPASS="$(get_env_var DB_PASSWORD)"
        case "$CUR_PGPASS" in
            ""|change-me*) set_env_var DB_PASSWORD "$(gen_password)"
                           ok "DB_PASSWORD сгенерирован (был заглушкой)." ;;
        esac
        case "$(get_env_var DB_NAME)" in
            ""|db.sqlite3) set_env_var DB_NAME "lms_db" ;;
        esac
        [ -z "$(get_env_var DB_USER)" ] && set_env_var DB_USER "lms_user"
        [ -z "$(get_env_var DB_HOST)" ] && set_env_var DB_HOST "db"
        [ -z "$(get_env_var DB_PORT)" ] && set_env_var DB_PORT "5432"
        info "DB_ENGINE уже postgresql — блок БД минимально дополнен."
    fi
    ok "PostgreSQL настроен в .env (в compose хост базы — «db»)."
else
    info "Режим SQLite (по умолчанию). Для прода с PostgreSQL: ./install.sh --postgres"
fi

# --------------------- 4. проверка и первичная инициализация -----------------
info "Шаг 4/4: проверка Django, миграции, статика"

if [ "$USE_POSTGRES" -eq 1 ]; then
    # В прод-режиме миграции применяются внутри контейнеров (make deploy POSTGRES=1),
    # здесь только синтаксическая проверка конфигурации.
    DJANGO_DEBUG=False "$PY_BIN" manage.py check && ok "manage.py check пройден."
    warn "Миграции PostgreSQL будут применены автоматически при: make deploy POSTGRES=1"
else
    DJANGO_DEBUG=True "$PY_BIN" manage.py migrate --noinput && ok "Миграции применены."
    "$PY_BIN" manage.py collectstatic --noinput >/dev/null 2>&1 || warn "collectstatic пропущен (нет STATIC_ROOT в dev — это нормально)."
    ok "Локальный запуск доступен: $PY_BIN manage.py runserver 0.0.0.0:8000"
fi

echo
ok "Установка завершена."
cat <<EOF

Дальнейшие шаги:
  * Суперпользователь:   $PY_BIN manage.py createsuperuser        (dev)
                         make superuser POSTGRES=1                  (прод)
  * Деплой в Docker:     make deploy            (SQLite)
                         make deploy POSTGRES=1 (PostgreSQL)
  * Перенос данных:      make sqlite-to-postgres POSTGRES=1
  * Значения .env:       grep -E '^(DB_|DJANGO_SECRET|DOMAIN)' .env
EOF
