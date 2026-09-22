import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from lms.models import Course, Enrollment, Homework, Lesson, Submission  # noqa: E402

User = get_user_model()

ok = True


def check(label, cond):
    global ok
    ok = ok and cond
    print(("OK  " if cond else "FAIL"), label)


# --- teacher creates course/lesson/homework via HTTP ---
t = Client()
t.force_login(User.objects.get(username="admin"))

r = t.post("/courses/new/", {"title": "ЕГЭ Математика",
           "subject": "MATH", "description": "Профиль"})
check("course create redirect", r.status_code == 302)
course = Course.objects.get(title="ЕГЭ Математика")
check("course teacher", course.teacher.username == "admin")

r = t.post(
    f"/courses/{course.pk}/lessons/new/",
    {"title": "Производная", "order": 1, "content": "Правила дифференцирования"},
)
check("lesson create", r.status_code == 302)
lesson = Lesson.objects.get(title="Производная")

deadline = (timezone.localtime() + timezone.timedelta(days=7)
            ).strftime("%Y-%m-%dT%H:%M")
r = t.post(
    f"/courses/{course.pk}/homework/new/",
    {"title": "ДЗ №1", "lesson": lesson.pk,
        "task": "Найти производную", "deadline": deadline},
)
check("homework create", r.status_code == 302)
hw = Homework.objects.get(title="ДЗ №1")

# --- student enrolls and submits ---
student, _ = User.objects.get_or_create(
    username="student1", defaults={"role": "STUDENT"})
student.role = "STUDENT"
student.set_password("student1")
student.save()

s = Client()
s.force_login(student)
check("student sees dashboard", s.get("/").status_code == 200)
check("course list for student", s.get("/courses/").status_code == 200)

r = s.get(f"/courses/{course.pk}/enroll/")
check("enroll redirect", r.status_code == 302)
check("enrollment exists", Enrollment.objects.filter(
    student=student, course=course).exists())

check("course detail access", s.get(
    f"/courses/{course.pk}/").status_code == 200)
check("lesson detail access", s.get(
    f"/lessons/{lesson.pk}/").status_code == 200)

r = s.post(f"/homework/{hw.pk}/submit/", {"answer": "f'(x) = 2x"})
check("submission create", r.status_code == 302)
sub = Submission.objects.get(homework=hw, student=student)
check("submission status NEW", sub.status == Submission.Status.NEW)

# --- teacher grades ---
r = t.post(f"/submissions/{sub.pk}/check/", {"status": "CHECKED",
           "grade": 95, "teacher_comment": "Отлично!"})
check("grading redirect", r.status_code == 302)
sub.refresh_from_db()
check("grade saved", sub.grade == 95 and sub.status == Submission.Status.CHECKED)
check("checked_at set", sub.checked_at is not None)

# --- role protection ---
r = s.get("/courses/new/")
check("student blocked from course_create (302)", r.status_code == 302)
r = t.get(f"/homework/{hw.pk}/submit/")
check("teacher blocked from submission_create (302)", r.status_code == 302)

# --- cleanup demo data ---
Submission.objects.filter(homework=hw).delete()
hw.delete()
Lesson.objects.filter(pk=lesson.pk).delete()
Enrollment.objects.filter(course=course).delete()
course.delete()
User.objects.filter(username="student1").delete()
print("demo data cleaned")

print("\nRESULT:", "ALL PASSED" if ok else "SOME CHECKS FAILED")
