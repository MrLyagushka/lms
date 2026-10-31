.DEFAULT_GOAL := help

# FIX: проброс профиля PostgreSQL. По умолчанию `make deploy` поднимает только
# SQLite-вариант; для прода с базой используйте POSTGRES=1, например:
#   make deploy POSTGRES=1
# Это добавляет «--profile postgres» к docker compose и включает сервис db.
COMPOSE = docker compose $(if $(POSTGRES),--profile postgres)

.PHONY: help up down restart logs logs-web status deploy shell migrate superuser check-domain init-env sqlite-to-postgres
help:            ## Показать список доступных команд
	@echo "Доступные команды:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Просто 'make' без аргументов показывает эту справку."
	@echo ""
	@echo "Для запуска с PostgreSQL добавьте POSTGRES=1, например: make deploy POSTGRES=1"

# FIX: деплой больше НЕ запускает init_env (раньше `make deploy` падал с
# [Errno 13] Permission denied на .env: контейнер работает от пользователя lms,
# а .env принадлежит root). Все переменные настраиваются один раз через
# ./install.sh или вручную; здесь только проверка, что .env существует.
# FIX: раньше здесь использовались `exec`-команды (wait_for_db, migrate,
# collectstatic). Контейнер lms_web запускает entrypoint со связкой
# «wait_for_db && migrate && collectstatic && gunicorn»: если база ещё не
# готова, exec попадает ровно в момент kill этого shell'а и получает
# SIGKILL — make падает с Error 137 (128+9), хотя проект штатно поднимается
# сам. Миграции и статика выполняются entrypoint'ом контейнера.
# FIX: убран «up -d --wait». Он делал деплой хрупким: любая временная
# нездоровость web (долгие первичные миграции, медленный старт) обрывала
# команду с «container is unhealthy», хотя через минуту всё стало бы хорошо.
# Теперь up просто поднимает контейнеры, а готовность ждётся отдельным
# циклом: опрос статуса каждые 5с до healthy; при exited/dead — мгновенный
# вывод логов и понятная ошибка; при превышении таймаута (по умолчанию
# WAIT_TIMEOUT=180 секунд) — вывод логов и предупреждение вместо падения.
WAIT_TIMEOUT ?= 180

up:              ## Собрать и запустить проект (POSTGRES=1 — с базой PostgreSQL)
	@test -f .env || (echo "[!] .env не найден. Выполните сначала: ./install.sh" && exit 1)
	$(COMPOSE) build
	$(COMPOSE) up -d
	@echo "[i] Ожидание готовности web (до $(WAIT_TIMEOUT)с)..."
	@end=0; i=0; \
	while true; do \
		st=$$($(COMPOSE) ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk '$$1=="web"{print $$2" "$$3}'); \
		case "$$st" in \
			running\ healthy) echo "[OK] web healthy"; break;; \
			exited*|dead*) echo "[!] Контейнер web завершился (статус: $$st). Последние логи:"; \
				$(COMPOSE) logs --tail=60 web; exit 1;; \
		esac; \
		i=$$((i+1)); \
		if [ $$((i*5)) -ge $(WAIT_TIMEOUT) ]; then \
			echo "[WARN] web не стал healthy за $(WAIT_TIMEOUT)с (статус: $$st). Приложение может ещё выполнять первичные миграции."; \
			echo "[i] Последние логи:"; $(COMPOSE) logs --tail=40 web; \
			echo "[i] Продолжайте наблюдать: make logs-web"; \
			break; \
		fi; \
		printf '.'; sleep 5; \
	done
	@echo "[OK] Деплой завершён. Статус: $(COMPOSE) ps | Логи: make logs"

down:            ## Остановить проект
	$(COMPOSE) down

restart:         ## Перезапустить проект
	$(COMPOSE) restart

logs:            ## Логи всех сервисов
	$(COMPOSE) logs -f

logs-web:        ## Логи только контейнера web
	$(COMPOSE) logs -f --tail=100 web

status:          ## Текущий статус контейнеров (state/health)
	$(COMPOSE) ps

deploy: up check-domain  ## Полный деплой: сначала запуск (up), потом проверка домена

shell:           ## Bash в контейнере web
	$(COMPOSE) exec web bash

migrate:         ## Применить миграции
	$(COMPOSE) exec web python manage.py migrate

superuser:       ## Создать суперпользователя
	$(COMPOSE) exec web python manage.py createsuperuser

check-domain:    ## Проверить соответствие домена в .env и Caddyfile
	$(COMPOSE) exec web python /app/scripts/check_domain.py

init-env:        ## Инициализировать .env (локально, от root; больше НЕ вызывается из up/deploy)
	@set -e; \
	if [ ! -f .env ] && [ ! -f .env.example ]; then \
		echo "[!] Нет ни .env, ни .env.example"; exit 1; \
	fi; \
	if [ ! -f .env ]; then cp .env.example .env; fi; \
	chmod 600 .env 2>/dev/null || true; \
	ARGS=""; \
	if [ -n "$(POSTGRES)" ]; then ARGS="--postgres -y"; fi; \
	python3 scripts/init_env.py $$ARGS

sqlite-to-postgres: ## Перенести данные из db.sqlite3 в PostgreSQL (make ... POSTGRES=1)
	@test -n "$(POSTGRES)" || (echo "[!] Запустите с POSTGRES=1: make sqlite-to-postgres POSTGRES=1" && exit 1)
	$(COMPOSE) exec -T web python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e admin.logentry --indent 2 > db_dump.json
	@echo "[i] Дамп SQLite сохранён в db_dump.json ($(shell wc -c < db_dump.json 2>/dev/null || echo '?') байт)"
	$(COMPOSE) exec -T web python manage.py loaddata db_dump.json
	@echo "[OK] Данные перенесены в PostgreSQL. Удалите дамп вручную: rm db_dump.json"
