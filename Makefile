.PHONY: up down restart logs deploy shell migrate superuser check-domain
# --- Основные команды ---
up:              ## Собрать и запустить проект
	docker compose build
	docker compose up -d
	docker compose exec web python manage.py migrate --noinput
	docker compose exec web python manage.py collectstatic --noinput

down:            ## Остановить проект
	docker compose down
restart:         ## Перезапустить проект
	docker compose restart
logs:            ## Логи всех сервисов
	docker compose logs -f

deploy: check-domain up  ## Полный деплой с проверкой домена
# --- Управление ---
shell:           ## Bash в контейнере web
	docker compose exec web bash
migrate:         ## Применить миграции
	docker compose exec web python manage.py migrate
superuser:       ## Создать суперпользователя
	docker compose exec web python manage.py createsuperuser
check-domain:    ## Проверить соответствие домена
	docker compose exec web python /app/scripts/check_domain.py
