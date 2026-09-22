# LMS ЕГЭ/ОГЭ — Система управления обучением
Веб-приложение на **Django 5** для онлайн-подготовки к ЕГЭ и ОГЭ: преподаватели создают курсы, уроки и домашние задания, ученики записываются и сдают работы на проверку.

- Регистрация доступна **только ученикам** (вход по email). Преподаватели создаются администратором.
- Поддерживаются **светлая/тёмная тема** (Tailwind `darkMode: 'class'`) и **кастомные темы** (настраиваются суперпользователем на странице `/theme-settings/`).
- Деплой через **Docker + Caddy** (автоматический HTTPS).

## Стек технологий
| Слой | Технология |
|------|-----------|
| Backend | Python 3.11+, Django 5.x |
| Frontend | HTML5 + Tailwind CSS (CDN) + Google Fonts (Inter) |
| База данных | SQLite (по умолчанию) / PostgreSQL |
| Dev-сервер | `python manage.py runserver` |
| Production | Gunicorn + Caddy (Docker) |

## Быстрый старт (локальная разработка)

1. Установи зависимости:
   ```
   pip install -r requirements.txt
   ```

2. Создай `.env` (скопируй из `.env.example`):
   ```
   cp .env.example .env
   ```
   Для локальной разработки можно оставить `DJANGO_DEBUG=True`.

3. Запусти миграции:
   ```
   python manage.py migrate
   ```

4. Создай суперпользователя:
   ```
   python manage.py createsuperuser
   ```

5. Запусти сервер:
   ```
   python manage.py runserver
   ```

Приложение будет доступно на http://127.0.0.1:8000
## Деплой через Docker
1. Настрой `.env` (укажи `DOMAIN_NAME`, `DJANGO_SECRET_KEY`, `CADDY_EMAIL` и др.).

2. Запусти деплой:
   ```
   make deploy
   ```

3. Или вручную:
   ```
   docker-compose build
   docker-compose up -d
   docker-compose exec web python manage.py migrate
   docker-compose exec web python manage.py collectstatic --noinput
   ```

Caddy автоматически получит HTTPS-сертификат для домена из `.env`.

## Управление через Makefile
- `make help` — список команд
- `make up` — запустить сервисы
- `make down` — остановить
- `make logs` — логи
- `make migrate` — миграции
- `make superuser` — создать админа
- `make check-domain` — проверка соответствия домена (.env ↔ Caddyfile)
- `make deploy` — полный деплой (check-domain + build + up + migrate + collectstatic)

## Структура проекта
```
сайт/
├── core/                # настройки Django
│   ├── settings.py      # конфиг (.env через python-dotenv, security, logging, БД)
│   ├── urls.py
│   └── wsgi.py
├── lms/                 # основное приложение
│   ├── models.py        # CustomUser, Course, Enrollment, Lesson, Homework, Submission, Theme
│   ├── views.py, forms.py, admin.py, urls.py
│   ├── decorators.py, context_processors.py
│   └── templates/lms/   # шаблоны
├── static/css/style.css
├── media/               # загрузки пользователей
├── scripts/             # вспомогательные скрипты
│   └── check_domain.py
├── Dockerfile
├── docker-compose.yml
├── Caddyfile
├── Makefile
├── .env / .env.example
├── .gitignore
├── requirements.txt
└── manage.py
```

## Модели данных
- **CustomUser** — вход по `email` (USERNAME_FIELD), роли TEACHER/STUDENT, `bio`, `avatar`.
- **Course** — курс: название, `slug`, предмет, описание, `is_published`, преподаватель, обложка.
- **Enrollment** — связь ученик ↔ курс.
- **Lesson** — урок курса (название, содержание, порядковый номер).
- **Homework** — ДЗ (курс, урок, задание, дедлайн).
- **Submission** — ответ ученика (текст + файл, статус, оценка, комментарий).
- **Theme** — кастомная тема оформления (активна только одна).

## Переменные окружения (.env)

| Переменная | Назначение |
|-----------|-----------|
| `DJANGO_SECRET_KEY` | Секретный ключ Django |
| `DJANGO_DEBUG` | Режим отладки (`True`/`False`) |
| `DJANGO_ALLOWED_HOSTS` | Разрешённые хосты (через запятую) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Доверенные источники CSRF |
| `DOMAIN_NAME` | Домен сайта |
| `SITE_URL` | Полный URL сайта |
| `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | База данных |
| `EMAIL_*` | Настройки почты |
| `CADDY_EMAIL` | Email для Let's Encrypt |
| `GUNICORN_WORKERS` | Кол-во воркеров gunicorn |

## Безопасность в production
Когда `DJANGO_DEBUG=False`, автоматически включаются:
- `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`
- HSTS (`SECURE_HSTS_SECONDS=31536000`, include subdomains, preload)
- `SECURE_PROXY_SSL_HEADER` для работы за прокси (Caddy)

Не забудьте задать собственный `DJANGO_SECRET_KEY`!

## Полезные команды
```powershell
python manage.py makemigrations   # создать миграции
python manage.py migrate          # применить миграции
python manage.py createsuperuser  # создать админа
python manage.py runserver        # запустить сервер
python manage.py collectstatic    # собрать статику
python manage.py check            # проверить конфигурацию
```