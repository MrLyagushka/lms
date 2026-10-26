"""Смоук-тест новых фич: привязка ученика, даты занятий по ученикам, настройки тем.

Запуск:  ./venv/bin/python smoke_test.py
"""
import os

import django
from django.test.utils import setup_test_environment

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()
setup_test_environment()

from django.test import Client  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from lms.models import Course, Enrollment, GradeLevel, Lesson, LessonDate, Subject, Theme  # noqa: E402

User = get_user_model()
ok = True

# Идемпотентность: убираем остатки предыдущих прогонов
User.objects.filter(email__in=["smoke-teacher@example.com", "smoke-student@example.com", "smoke-admin@example.com"]).delete()
Course.objects.filter(title="Смоук-курс").delete()
Theme.objects.filter(name__startswith="Смоук-тема").delete()


def check(label, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        ok = False


teacher, _ = User.objects.get_or_create(
    email="smoke-teacher@example.com",
    defaults={"first_name": "Смоук", "last_name": "Педагог", "role": User.Role.TEACHER},
)
teacher.set_password("pass12345")
teacher.save()

student, _ = User.objects.get_or_create(
    email="smoke-student@example.com",
    defaults={"first_name": "Смоук", "last_name": "Ученик", "role": User.Role.STUDENT},
)
student.set_password("pass12345")
student.save()

course, _ = Course.objects.get_or_create(
    title="Смоук-курс",
    defaults={"teacher": teacher, "subject": Subject.MATH, "grade_level": GradeLevel.G11,
              "description": "Тестовый курс", "is_published": True},
)
lesson, _ = Lesson.objects.get_or_create(course=course, title="Смоук-урок", defaults={"order": 1})

c = Client()
c.force_login(teacher)

# --- 1. Привязка ученика по email -------------------------------------------------------
r = c.post(f"/courses/{course.pk}/enroll-student/", {"email": student.email})
check("enroll-student POST -> redirect", r.status_code == 302)
check("enrollment created", Enrollment.objects.filter(course=course, student=student).exists())

r = c.post(f"/courses/{course.pk}/enroll-student/", {"email": student.email})
check("duplicate enroll shows form error (200, no crash)", r.status_code == 200)

# --- 2. Назначение даты занятия ученику -------------------------------------------------
r = c.get(f"/lessons/{lesson.pk}/schedule/")
check("schedule GET 200", r.status_code == 200)
check("schedule has rows", r.context is not None and len(r.context["rows"]) == 1)

enrollment = Enrollment.objects.get(course=course, student=student)
r = c.post(f"/lessons/{lesson.pk}/schedule/", {
    f"e{enrollment.pk}-date": "2026-11-05T18:30",
})
check("schedule POST -> redirect", r.status_code == 302)
ld = LessonDate.objects.filter(lesson=lesson, enrollment=enrollment).first()
from django.utils import timezone as _tz  # noqa: E402
check("lesson date saved", ld is not None and _tz.localtime(ld.date).strftime("%Y-%m-%d %H:%M") == "2026-11-05 18:30")

# --- 3. Ученик видит свою дату ---------------------------------------------------------
c2 = Client()
c2.force_login(student)
r = c2.get(lesson.get_absolute_url())
check("lesson_detail for student 200", r.status_code == 200)
check("student sees own date", b"18:30" in r.content)

r = c2.get(course.get_absolute_url())
check("course_list/detail for student 200", r.status_code == 200)
check("student course visible in list",
      b"\xd0\xa1\xd0\xbc\xd0\xbe\xd1\x83\xd0\xba" in c2.get("/courses/").content)  # "Смоук"

# --- 4. Настройки тем -------------------------------------------------------------------
c.force_login(User.objects.create_superuser(email="smoke-admin@example.com", password="pass12345"))
r = c.get("/theme-settings/")
check("theme-settings GET 200 (no TemplateSyntaxError)", r.status_code == 200)

r = c.post("/theme-settings/", {
    "name": "Смоук-тема", "primary_color": "#10b981", "secondary_color": "#3b82f6",
    "background_color": "#ffffff", "text_color": "#111827", "card_background": "#f9fafb",
    "navbar_background": "#ffffff", "font_family": "Inter, sans-serif",
    "border_radius": "12px", "is_active": "on",
})
check("theme create POST -> redirect", r.status_code == 302)
theme = Theme.objects.filter(name="Смоук-тема").first()
check("theme saved + active", theme is not None and theme.is_active)

# редактирование: битое значение цвета -> форма с ошибками, без падения
r = c.post("/theme-settings/", {
    "theme_id": theme.pk, "name": "Смоук-тема", "primary_color": "notacolor",
    "secondary_color": "#3b82f6", "background_color": "#ffffff", "text_color": "#111827",
    "card_background": "#f9fafb", "navbar_background": "#ffffff",
    "font_family": "Inter, sans-serif", "border_radius": "12px", "is_active": "on",
})
errors = r.context["form"].errors if r.context else {}
check("invalid color re-renders form with errors", r.status_code == 200 and "primary_color" in errors)

# сохранение через форму с редактированием (pop-up datalist + свободный ввод шрифта)
r = c.post("/theme-settings/", {
    "theme_id": theme.pk, "name": "Смоук-тема 2", "primary_color": "#10b981",
    "secondary_color": "#3b82f6", "background_color": "#ffffff", "text_color": "#111827",
    "card_background": "#f9fafb", "navbar_background": "#ffffff",
    "font_family": "Roboto, sans-serif", "border_radius": "8px",
})
check("theme edit POST -> redirect", r.status_code == 302)
theme.refresh_from_db()
check("theme edited (font saved)", theme.font_family == "Roboto, sans-serif" and theme.name == "Смоук-тема 2")

# --- cleanup -------------------------------------------------------------------------------
LessonDate.objects.filter(lesson=lesson).delete()
Enrollment.objects.filter(course=course, student=student).delete()
theme.delete()
lesson.delete()
course.delete()
User.objects.filter(email__endswith="smoke@example.com").delete()
User.objects.filter(email__in=["smoke-teacher@example.com", "smoke-student@example.com", "smoke-admin@example.com"]).delete()

print("\nALL OK" if ok else "\nSOME CHECKS FAILED")
raise SystemExit(0 if ok else 1)
