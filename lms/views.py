import json
from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Avg, Count, Min, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_GET

from .autograding import grade_multi
from .decorators import student_required, teacher_required
from .features import courses_enabled
from .forms import (
    CourseForm,
    EnrollStudentForm,
    FONT_CHOICES,
    GradeForm,
    HomeworkForm,
    HomeworkIssueForm,
    HomeworkIssueStudentsForm,
    HomeworkProblemFormSet,
    LessonDateForm,
    LessonForm,
    LinkStudentForm,
    ProfileForm,
    StudentManageForm,
    SubmissionForm,
    ThemeForm,
    UserRegistrationForm,
)
from .models import (
    Course,
    Enrollment,
    Homework,
    HomeworkIssue,
    HomeworkProblem,
    Lesson,
    LessonDate,
    StudentTeacherLink,
    Submission,
    SubmissionAnswer,
    Theme,
)

User = get_user_model()


def _courses_hidden_response(request):
    """Курсы временно скрыты (добавим в обновлениях). Функционал не удалён.

    NOTE: включается обратно переменной окружения FEATURE_COURSES=True
    (см. lms/features.py).
    """
    if not courses_enabled():
        messages.info(request, "Курсы мы добавим в ближайших обновлениях.")
        return redirect("dashboard")
    return None


def _homeworks_of_teacher(teacher):
    """Все ДЗ учителя: прямые выдачи + курсовые (курсы скрыты до обновлений)."""
    if teacher.is_superuser:
        return Homework.objects.all()
    return Homework.objects.filter(
        Q(issues__teacher=teacher) | Q(course__teacher=teacher)
    ).distinct()


def _issue_homework_to_students(homework, students, deadline=None, teacher=None):
    """Создаёт/обновляет строки выдачи ДЗ ученикам (по привязке к учителю)."""
    now = timezone.now()
    existing = {
        i.student_id: i
        for i in HomeworkIssue.objects.filter(homework=homework, student_id__in=[s.pk for s in students])
    }
    to_update = []
    for student in students:
        issue = existing.get(student.pk)
        if issue is None:
            issue = HomeworkIssue(
                homework=homework,
                student=student,
                teacher=(homework.course.teacher if homework.course_id else None) or teacher,
            )
        if not issue.issued:
            issue.issued_at = now
        issue.issued = True
        if deadline is not None:
            issue.deadline = deadline
        to_update.append(issue)
    HomeworkIssue.objects.bulk_create(to_update, update_conflicts=True,
                                      unique_fields=["homework", "student"],
                                      update_fields=["issued", "issued_at", "deadline"])
    return len(to_update)


def _sync_issues_for_enrollment(enrollment):
    """Создаёт строки выдачи ДЗ для всех заданий курса этого ученика."""
    existing = set(
        HomeworkIssue.objects.filter(enrollment=enrollment).values_list("homework_id", flat=True)
    )
    HomeworkIssue.objects.bulk_create(
        HomeworkIssue(enrollment=enrollment, homework_id=hw_id)
        for hw_id in Homework.objects.filter(course=enrollment.course).values_list("pk", flat=True)
        if hw_id not in existing
    )


def _sync_issues_for_homework(homework):
    """Создаёт строки выдачи ДЗ для всех учеников курса этого задания."""
    existing = set(
        HomeworkIssue.objects.filter(homework=homework).values_list("enrollment_id", flat=True)
    )
    HomeworkIssue.objects.bulk_create(
        HomeworkIssue(homework=homework, enrollment_id=enr_id)
        for enr_id in Enrollment.objects.filter(course=homework.course).values_list("pk", flat=True)
        if enr_id not in existing
    )


def _apply_conduct_issues(lesson_date):
    """Выдаёт/отзывает автовыдаваемые ДЗ урока при смене отметки о проведении."""
    issues = HomeworkIssue.objects.filter(
        enrollment=lesson_date.enrollment,
        homework__lesson=lesson_date.lesson,
        auto_issue=True,
    )
    if lesson_date.is_conducted:
        issues.filter(issued=False).update(issued=True, issued_at=timezone.now())
    else:
        issues.update(issued=False, issued_at=None)


# --------------------------------------------------------------------------- #
#  Публичные страницы
# --------------------------------------------------------------------------- #
def landing(request):
    """Рекламный лендинг — доступен без авторизации."""
    published_courses = (
        Course.objects.filter(is_published=True)
        .select_related("teacher")
        .annotate(
            num_lessons=Count("lessons", distinct=True),
            num_students=Count("enrollments", distinct=True),
        )
    )
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "lms/landing.html", {"courses": published_courses})


@require_GET
def secure_media(request, name):
    """Отдаёт media только пользователю, которому разрешен доступ к объекту."""
    # FIX: media больше не раздается Caddy напрямую, иначе URL файла обходил ACL.
    if not request.user.is_authenticated or not name or ".." in name or "\\" in name:
        raise Http404

    submission = Submission.objects.select_related(
        "student", "homework__course", "homework__issues__teacher"
    ).filter(file=name).first()
    if submission:
        allowed = (
            request.user.is_superuser
            or request.user == submission.student
            or submission.homework.teacher == request.user
        )
    else:
        avatar = User.objects.filter(avatar=name).first()
        cover = Course.objects.filter(cover=name).first()
        allowed = bool(avatar or cover)
        if avatar:
            allowed = request.user.is_authenticated
        if cover:
            allowed = (
                cover.is_published
                or cover.teacher_id == request.user.id
                or Enrollment.objects.filter(course=cover, student=request.user).exists()
                or request.user.is_superuser
            )

    if not allowed or not default_storage.exists(name):
        raise Http404

    try:
        file_handle = default_storage.open(name, "rb")
    except (FileNotFoundError, OSError):
        raise Http404 from None
    return FileResponse(file_handle, as_attachment=name.startswith("submissions/"), filename=name.rsplit("/", 1)[-1])


# --------------------------------------------------------------------------- #
#  Регистрация
# --------------------------------------------------------------------------- #
def register(request):
    """Регистрация нового пользователя (email + пароль) с авто-входом."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            user = authenticate(
                request,
                username=user.email,
                password=form.cleaned_data["password"],
            )
            if user is not None:
                login(request, user)
                messages.success(request, "Добро пожаловать! Регистрация завершена.")
                return redirect("dashboard")
    else:
        form = UserRegistrationForm()

    return render(request, "lms/register.html", {"form": form})


# --------------------------------------------------------------------------- #
#  Dashboard
# --------------------------------------------------------------------------- #
@login_required
def dashboard(request):
    """Главная страница в зависимости от роли."""
    if request.user.is_teacher:
        submissions = (
            Submission.objects.filter(
                Q(homework__course__teacher=request.user)
                | Q(homework__issues__teacher=request.user)
            )
            .select_related("student", "homework")
            .distinct()
        )
        context = {
            "courses": [],
            "courses_enabled": courses_enabled(),
            "courses_count": 0,
            "students_count": StudentTeacherLink.objects.filter(
                teacher=request.user
            ).count(),
            "homework_count": _homeworks_of_teacher(request.user).count(),
            "new_submissions_count": submissions.filter(
                status=Submission.Status.NEW
            ).count(),
            "recent_submissions": submissions[:5],
        }
        return render(request, "lms/dashboard_teacher.html", context)

    # Ученик
    issues = (
        HomeworkIssue.objects.filter(student=request.user, issued=True)
        .select_related("homework", "teacher")
    )
    submissions = Submission.objects.filter(student=request.user).select_related(
        "homework", "homework__course"
    )
    context = {
        "courses": [],
        "courses_enabled": courses_enabled(),
        "courses_count": 0,
        "teacher": getattr(request.user.teacher_link, "teacher", None),
        "issued_homeworks": issues[:8],
        "issued_count": issues.count(),
        "submissions_count": submissions.count(),
        "checked_count": submissions.filter(status=Submission.Status.CHECKED).count(),
        "pending_count": submissions.filter(status=Submission.Status.NEW).count(),
        "recent_submissions": submissions[:5],
    }
    return render(request, "lms/dashboard_student.html", context)


# --------------------------------------------------------------------------- #
#  Профиль
# --------------------------------------------------------------------------- #
@login_required
def profile(request):
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Профиль обновлён.")
            return redirect("profile")
    else:
        form = ProfileForm(instance=request.user)
    return render(request, "lms/profile.html", {"form": form})


# --------------------------------------------------------------------------- #
#  Курсы
# --------------------------------------------------------------------------- #
@login_required
def course_list(request):
    """Список всех курсов с возможностью фильтра по предмету."""
    courses = Course.objects.select_related("teacher").annotate(
        num_lessons=Count("lessons", distinct=True),
        num_students=Count("enrollments", distinct=True),
        num_homeworks=Count("homeworks", distinct=True),
    )

    # FIX: ученик не видит неопубликованные чужие курсы, преподаватель — чужие черновики.
    # FIX: курс виден ученику, если он опубликован ИЛИ ученик привязан к курсу (записан).
    # Используем подзапрос по pk, чтобы join по enrollments не искажал аннотации (число учеников).
    if request.user.is_teacher:
        courses = courses.filter(Q(is_published=True) | Q(teacher=request.user))
    else:
        enrolled_ids = Enrollment.objects.filter(student=request.user).values("course_id")
        courses = courses.filter(Q(is_published=True) | Q(pk__in=enrolled_ids))

    subject = request.GET.get("subject")
    if subject:
        courses = courses.filter(subject=subject)

    grade = request.GET.get("grade")
    if grade:
        courses = courses.filter(grade_level=grade)

    query = request.GET.get("q")
    if query:
        courses = courses.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )

    my_course_ids = []
    if request.user.is_student:
        my_course_ids = list(
            Enrollment.objects.filter(student=request.user).values_list(
                "course_id", flat=True
            )
        )

    context = {
        "courses": courses,
        "subjects": Course._meta.get_field("subject").choices,
        "grades": Course._meta.get_field("grade_level").choices,
        "current_subject": subject,
        "current_grade": grade,
        "query": query or "",
        "my_course_ids": my_course_ids,
    }
    return render(request, "lms/course_list.html", context)


@login_required
def course_detail(request, pk):
    course = get_object_or_404(Course.objects.select_related("teacher"), pk=pk)
    is_owner = request.user == course.teacher
    is_enrolled = Enrollment.objects.filter(
        student=request.user, course=course
    ).exists()

    # FIX: не раскрываем даже метаданные приватного курса постороннему пользователю.
    if not course.is_published and not (is_owner or is_enrolled or request.user.is_superuser):
        return redirect("course_list")

    # Доступ к содержанию: преподаватель-владелец либо записанный ученик
    can_view_content = is_owner or is_enrolled

    if can_view_content:
        lessons = list(course.lessons.all().select_related("course"))
        if is_owner:
            homeworks = list(course.homeworks.all().select_related("lesson"))
        else:
            # Ученик не видит невыданные автовыдаваемые задания.
            unissued = HomeworkIssue.objects.filter(
                enrollment__course=course,
                enrollment__student=request.user,
                auto_issue=True,
                issued=False,
            ).values_list("homework_id", flat=True)
            homeworks = list(
                course.homeworks.exclude(pk__in=unissued).select_related("lesson")
            )
            # FIX: дедлайн ДЗ теперь индивидуальный — прикладываем выдачу ученика.
            issues = {
                i.homework_id: i
                for i in HomeworkIssue.objects.filter(
                    enrollment__course=course, enrollment__student=request.user
                )
            }
            for hw in homeworks:
                hw.my_issue = issues.get(hw.pk)
        # FIX: дата и отметка о проведении теперь индивидуальны для каждого ученика
        # (назначаются преподавателем в панели ученика после привязки к курсу).
        my_dates = {}
        if is_enrolled:
            my_dates = {
                d.lesson_id: d
                for d in LessonDate.objects.filter(
                    enrollment__course=course, enrollment__student=request.user
                )
            }
        for l in lessons:
            lesson_date = my_dates.get(l.pk)
            l.my_date = lesson_date.date if lesson_date else None
            l.my_conducted = bool(lesson_date and lesson_date.is_conducted)
    else:
        lessons = []
        homeworks = []

    context = {
        "course": course,
        "lessons": lessons,
        "homeworks": homeworks,
        "is_owner": is_owner,
        "is_enrolled": is_enrolled,
        "can_view_content": can_view_content,
        "students": course.enrollments.select_related("student")
        if is_owner
        else [],
    }
    return render(request, "lms/course_detail.html", context)


@teacher_required
def course_create(request):
    if request.method == "POST":
        form = CourseForm(request.POST, request.FILES)
        if form.is_valid():
            course = form.save(commit=False)
            course.teacher = request.user
            course.save()
            messages.success(request, "Курс создан.")
            return redirect(course.get_absolute_url())
    else:
        form = CourseForm()
    return render(request, "lms/course_form.html", {"form": form, "title": "Новый курс"})


@teacher_required
def course_edit(request, pk):
    course = get_object_or_404(Course, pk=pk, teacher=request.user)
    if request.method == "POST":
        form = CourseForm(request.POST, request.FILES, instance=course)
        if form.is_valid():
            form.save()
            messages.success(request, "Курс обновлён.")
            return redirect(course.get_absolute_url())
    else:
        form = CourseForm(instance=course)
    return render(
        request,
        "lms/course_form.html",
        {"form": form, "title": "Редактирование курса", "course": course},
    )


@teacher_required
def course_delete(request, pk):
    course = get_object_or_404(Course, pk=pk, teacher=request.user)
    if request.method == "POST":
        course.delete()
        messages.success(request, "Курс удалён.")
        return redirect("course_list")
    return render(request, "lms/confirm_delete.html", {"object": course, "cancel_url": course.get_absolute_url()})


@student_required
def enroll(request, pk):
    course = get_object_or_404(Course, pk=pk, is_published=True)
    enrollment, created = Enrollment.objects.get_or_create(student=request.user, course=course)
    if created:
        _sync_issues_for_enrollment(enrollment)
    messages.success(request, f"Вы записаны на курс «{course.title}».")
    return redirect(course.get_absolute_url())


@student_required
def unenroll(request, pk):
    course = get_object_or_404(Course, pk=pk)
    Enrollment.objects.filter(student=request.user, course=course).delete()
    messages.info(request, f"Вы отписаны от курса «{course.title}».")
    return redirect(course.get_absolute_url())


# --------------------------------------------------------------------------- #
#  Уроки
# --------------------------------------------------------------------------- #
@login_required
def lesson_detail(request, pk):
    lesson = get_object_or_404(
        Lesson.objects.select_related("course", "course__teacher"), pk=pk
    )
    course = lesson.course
    is_owner = request.user == course.teacher
    is_enrolled = Enrollment.objects.filter(
        student=request.user, course=course
    ).exists()
    if not (is_owner or is_enrolled):
        messages.error(request, "Сначала запишитесь на курс.")
        return redirect(course.get_absolute_url())

    if is_owner:
        homeworks = list(lesson.homeworks.all())
    else:
        # FIX: невыданные автовыдаваемые задания скрыты (настройка на ученика).
        unissued = HomeworkIssue.objects.filter(
            enrollment__course=course,
            enrollment__student=request.user,
            homework__lesson=lesson,
            auto_issue=True,
            issued=False,
        ).values_list("homework_id", flat=True)
        homeworks = list(lesson.homeworks.exclude(pk__in=unissued))
        # FIX: дедлайн ДЗ теперь индивидуальный — прикладываем выдачу ученика.
        issues = {
            i.homework_id: i
            for i in HomeworkIssue.objects.filter(
                enrollment__course=course,
                enrollment__student=request.user,
                homework__lesson=lesson,
            )
        }
        for hw in homeworks:
            hw.my_issue = issues.get(hw.pk)

    # FIX: дата и отметка о проведении — индивидуальны для каждого ученика.
    my_date = None
    my_conducted = False
    if is_enrolled:
        lesson_date = LessonDate.objects.filter(
            enrollment__course=course,
            enrollment__student=request.user,
            lesson=lesson,
        ).first()
        if lesson_date:
            my_date = lesson_date.date
            my_conducted = lesson_date.is_conducted

    context = {
        "lesson": lesson,
        "course": course,
        "is_owner": is_owner,
        "homeworks": homeworks,
        "my_date": my_date,
        "my_conducted": my_conducted,
    }
    return render(request, "lms/lesson_detail.html", context)


@teacher_required
def lesson_create(request, course_pk):
    course = get_object_or_404(Course, pk=course_pk, teacher=request.user)
    if request.method == "POST":
        form = LessonForm(request.POST)
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.course = course
            lesson.save()
            messages.success(request, "Урок добавлен.")
            return redirect(course.get_absolute_url())
    else:
        next_order = course.lessons.count() + 1
        form = LessonForm(initial={"order": next_order})
    return render(
        request,
        "lms/lesson_form.html",
        {"form": form, "course": course, "title": "Новый урок"},
    )


@teacher_required
def lesson_edit(request, pk):
    lesson = get_object_or_404(Lesson, pk=pk, course__teacher=request.user)
    if request.method == "POST":
        form = LessonForm(request.POST, instance=lesson)
        if form.is_valid():
            form.save()
            messages.success(request, "Урок обновлён.")
            return redirect(lesson.get_absolute_url())
    else:
        form = LessonForm(instance=lesson)
    return render(
        request,
        "lms/lesson_form.html",
        {"form": form, "course": lesson.course, "lesson": lesson, "title": "Редактирование урока"},
    )


@teacher_required
def lesson_delete(request, pk):
    lesson = get_object_or_404(Lesson, pk=pk, course__teacher=request.user)
    course = lesson.course
    if request.method == "POST":
        lesson.delete()
        messages.success(request, "Урок удалён.")
        return redirect(course.get_absolute_url())
    return render(
        request,
        "lms/confirm_delete.html",
        {"object": lesson, "cancel_url": course.get_absolute_url()},
    )


@teacher_required
def course_enroll_student(request, pk):
    """Преподаватель привязывает ученика к курсу (по email)."""
    course = get_object_or_404(Course, pk=pk, teacher=request.user)
    if request.method == "POST":
        form = EnrollStudentForm(request.POST, course=course)
        if form.is_valid():
            enrollment = Enrollment.objects.create(student=form.student, course=course)
            _sync_issues_for_enrollment(enrollment)
            messages.success(
                request,
                f"{form.student.full_name} привязан(а) к курсу «{course.title}». "
                f"Назначьте даты занятий в расписании уроков.",
            )
            return redirect(course.get_absolute_url())
    else:
        form = EnrollStudentForm(course=course)
    return render(
        request,
        "lms/course_enroll_student.html",
        {"course": course, "form": form},
    )


@teacher_required
def course_unenroll_student(request, pk, enrollment_pk):
    """Преподаватель отвязывает ученика от курса."""
    course = get_object_or_404(Course, pk=pk, teacher=request.user)
    enrollment = get_object_or_404(Enrollment, pk=enrollment_pk, course=course)
    if request.method == "POST":
        student_name = enrollment.student.full_name
        enrollment.delete()
        messages.info(request, f"{student_name} отвязан(а) от курса «{course.title}».")
    return redirect(course.get_absolute_url())


@teacher_required
@transaction.atomic
def student_schedule(request, pk):
    """Панель ученика: даты занятий и отметки о проведении по каждому уроку.

    Дата и отметка «проведено» хранятся отдельно для каждого ученика
    (LessonDate). При отметке «проведено» автоматически выдаются ДЗ урока,
    у которых у этого ученика включена автовыдача (HomeworkIssue.auto_issue).
    """
    student = _managed_student_or_404(request, pk)
    teacher = request.user

    if request.method == "POST":
        enrollment = get_object_or_404(
            Enrollment, pk=request.POST.get("enrollment"), student=student
        )
        if not teacher.is_superuser and enrollment.course.teacher != teacher:
            raise Http404
        existing = {
            d.lesson_id: d for d in LessonDate.objects.filter(enrollment=enrollment)
        }
        has_errors = False
        for lesson in enrollment.course.lessons.all():
            prefix = f"l{lesson.pk}"
            instance = existing.get(lesson.pk)
            raw = request.POST.get(f"{prefix}-date", "").strip()
            if not raw:
                # Пустое поле — запись снимается (автовыдача отзывается).
                if instance:
                    if instance.is_conducted:
                        HomeworkIssue.objects.filter(
                            enrollment=enrollment,
                            homework__lesson=lesson,
                            auto_issue=True,
                        ).update(issued=False, issued_at=None)
                    instance.delete()
                continue
            form = LessonDateForm(request.POST, instance=instance, prefix=prefix)
            if form.is_valid():
                obj = form.save(commit=False)
                obj.enrollment = enrollment
                obj.lesson = lesson
                was_conducted = bool(instance and instance.is_conducted)
                if obj.is_conducted and not obj.conducted_at:
                    obj.conducted_at = timezone.now()
                if not obj.is_conducted:
                    obj.conducted_at = None
                obj.save()
                if obj.is_conducted != was_conducted:
                    _apply_conduct_issues(obj)
            else:
                has_errors = True
        if has_errors:
            messages.error(request, "Проверьте формат даты (например 25.10.2026 18:00).")
        else:
            messages.success(request, "Расписание занятий сохранено.")
        return redirect("student_schedule", pk=student.pk)

    enrollments = student.enrollments.select_related("course").order_by("course__title")
    if not teacher.is_superuser:
        enrollments = enrollments.filter(course__teacher=teacher)
    rows = []
    for enrollment in enrollments:
        dates = {
            d.lesson_id: d
            for d in LessonDate.objects.filter(enrollment=enrollment)
        }
        lessons = [
            {
                "lesson": lesson,
                "date_obj": dates.get(lesson.pk),
                "form": LessonDateForm(
                    instance=dates.get(lesson.pk)
                    or LessonDate(enrollment=enrollment, lesson=lesson),
                    prefix=f"l{lesson.pk}",
                ),
            }
            for lesson in enrollment.course.lessons.all()
        ]
        rows.append({"enrollment": enrollment, "lessons": lessons})
    return render(
        request,
        "lms/student_schedule.html",
        {"student": student, "rows": rows},
    )


@teacher_required
@transaction.atomic
def student_issues(request, pk):
    """Панель ученика: автовыдача ДЗ, выдача и дедлайны индивидуально."""
    student = _managed_student_or_404(request, pk)
    teacher = request.user

    if request.method == "POST":
        enrollment = get_object_or_404(
            Enrollment, pk=request.POST.get("enrollment"), student=student
        )
        if not teacher.is_superuser and enrollment.course.teacher != teacher:
            raise Http404
        existing = {
            i.homework_id: i
            for i in HomeworkIssue.objects.filter(enrollment=enrollment)
        }
        has_errors = False
        for homework in enrollment.course.homeworks.all().select_related("lesson"):
            prefix = f"h{homework.pk}"
            instance = existing.get(homework.pk)
            form = HomeworkIssueForm(
                request.POST,
                instance=instance or HomeworkIssue(enrollment=enrollment, homework=homework),
                prefix=prefix,
            )
            if form.is_valid():
                obj = form.save(commit=False)
                if obj.issued and not obj.issued_at:
                    obj.issued_at = timezone.now()
                if not obj.issued:
                    obj.issued_at = None
                obj.save()
            else:
                has_errors = True
        if has_errors:
            messages.error(request, "Проверьте формат дедлайна (например 25.10.2026 18:00).")
        else:
            messages.success(request, "Настройки выдачи ДЗ сохранены.")
        return redirect("student_issues", pk=student.pk)

    enrollments = student.enrollments.select_related("course").order_by("course__title")
    if not teacher.is_superuser:
        enrollments = enrollments.filter(course__teacher=teacher)
    rows = []
    for enrollment in enrollments:
        issues = {
            i.homework_id: i
            for i in HomeworkIssue.objects.filter(enrollment=enrollment)
        }
        homeworks = [
            {
                "homework": homework,
                "issue": issues.get(homework.pk),
                "form": HomeworkIssueForm(
                    instance=issues.get(homework.pk)
                    or HomeworkIssue(enrollment=enrollment, homework=homework),
                    prefix=f"h{homework.pk}",
                ),
            }
            for homework in enrollment.course.homeworks.select_related("lesson")
        ]
        rows.append({"enrollment": enrollment, "homeworks": homeworks})
    return render(
        request,
        "lms/student_issues.html",
        {"student": student, "rows": rows},
    )


# --------------------------------------------------------------------------- #
#  Домашние задания
# --------------------------------------------------------------------------- #
@login_required
def homework_detail(request, pk):
    homework = get_object_or_404(
        Homework.objects.select_related("course", "course__teacher", "lesson"), pk=pk
    )
    course = homework.course
    is_owner = request.user == course.teacher
    is_enrolled = Enrollment.objects.filter(
        student=request.user, course=course
    ).exists()
    if not (is_owner or is_enrolled):
        messages.error(request, "Нет доступа к этому заданию.")
        return redirect(course.get_absolute_url())

    # FIX: выдача ДЗ теперь индивидуальна — проверка по строке HomeworkIssue ученика.
    my_issue = None
    if is_enrolled:
        my_issue = HomeworkIssue.objects.filter(
            homework=homework, enrollment__student=request.user
        ).first()
    if not is_owner and my_issue is not None and not my_issue.is_available:
        messages.info(request, "Задание ещё не выдано. Оно появится после проведения занятия.")
        return redirect(course.get_absolute_url())

    my_submission = None
    submissions = None
    my_answers = {}
    if is_owner:
        submissions = homework.submissions.select_related("student")
    elif is_enrolled:
        my_submission = homework.submissions.filter(student=request.user).first()
        if my_submission:
            my_answers = {a.problem_id: a.answer for a in my_submission.answers.all()}

    context = {
        "homework": homework,
        "course": course,
        "is_owner": is_owner,
        "my_submission": my_submission,
        "my_answers": my_answers,
        "submissions": submissions,
        "my_issue": my_issue,
        "now": timezone.now(),
    }
    return render(request, "lms/homework_detail.html", context)


def _renumber_problems(homework):
    """Присваивает задачам sequential номера 1..N (после удаления строк formset'ом)."""
    problems = list(homework.problems.all())
    # FIX: два прохода, чтобы не нарушить unique_together (homework, order).
    for idx, problem in enumerate(problems, start=1):
        if problem.order != 900 + idx:
            problem.order = 900 + idx
            problem.save(update_fields=["order"])
    for idx, problem in enumerate(problems, start=1):
        if problem.order != idx:
            problem.order = idx
            problem.save(update_fields=["order"])


@teacher_required
def homework_create(request, course_pk):
    course = get_object_or_404(Course, pk=course_pk, teacher=request.user)
    if request.method == "POST":
        form = HomeworkForm(request.POST, course=course)
        formset = HomeworkProblemFormSet(request.POST, request.FILES, prefix="problems")
        if form.is_valid() and formset.is_valid():
            homework = form.save(commit=False)
            homework.course = course
            homework.save()
            formset.instance = homework
            formset.save()
            _renumber_problems(homework)
            # FIX: создаём строки выдачи ДЗ для всех уже привязанных учеников.
            _sync_issues_for_homework(homework)
            messages.success(request, "Домашнее задание создано.")
            return redirect(homework.get_absolute_url())
    else:
        form = HomeworkForm(course=course)
        formset = HomeworkProblemFormSet(prefix="problems")
    return render(
        request,
        "lms/homework_form.html",
        {"form": form, "formset": formset, "course": course, "title": "Новое домашнее задание"},
    )


@teacher_required
def homework_edit(request, pk):
    homework = get_object_or_404(Homework, pk=pk, course__teacher=request.user)
    if request.method == "POST":
        form = HomeworkForm(request.POST, instance=homework, course=homework.course)
        formset = HomeworkProblemFormSet(request.POST, request.FILES, instance=homework, prefix="problems")
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            _renumber_problems(homework)
            messages.success(request, "Домашнее задание обновлено.")
            return redirect(homework.get_absolute_url())
    else:
        form = HomeworkForm(instance=homework, course=homework.course)
        formset = HomeworkProblemFormSet(instance=homework, prefix="problems")
    return render(
        request,
        "lms/homework_form.html",
        {
            "form": form,
            "formset": formset,
            "course": homework.course,
            "homework": homework,
            "title": "Редактирование ДЗ",
        },
    )


@teacher_required
def homework_delete(request, pk):
    homework = get_object_or_404(Homework, pk=pk, course__teacher=request.user)
    course = homework.course
    if request.method == "POST":
        homework.delete()
        messages.success(request, "Домашнее задание удалено.")
        return redirect(course.get_absolute_url())
    return render(
        request,
        "lms/confirm_delete.html",
        {"object": homework, "cancel_url": course.get_absolute_url()},
    )


# --------------------------------------------------------------------------- #
#  Импорт уроков и ДЗ из JSON
# --------------------------------------------------------------------------- #
def _import_dt(value, field, path, errors):
    """Парсит ISO-строку («2025-09-15» / «2025-09-15 18:00») в aware datetime."""
    if value in (None, ""):
        return None
    dt = None
    if isinstance(value, str):
        dt = parse_datetime(value.strip())
        if dt is None:
            day = parse_date(value.strip())
            if day is not None:
                dt = datetime.combine(day, time())
    if dt is None:
        errors.append(f"{path}: поле «{field}» — не дата (нужно «2025-09-15» или «2025-09-15 18:00»).")
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _import_answer_type(value, field, path, errors):
    """Возвращает корректный Homework.AnswerType по строке из JSON."""
    if value in (None, ""):
        return Homework.AnswerType.NONE
    candidate = str(value).strip().upper()
    if candidate not in Homework.AnswerType.values:
        errors.append(f"{path}: поле «{field}» — неизвестный тип ответа «{value}». "
                      f"Допустимо: {', '.join(Homework.AnswerType.values)}.")
        return Homework.AnswerType.NONE
    return candidate


def _import_homework(course, data, path, errors, lesson=None):
    """Создаёт Homework (+ задачи) из словаря JSON. Возвращает объект.

    Ключи «auto_issue», «issued», «deadline» применяются как значения
    по умолчанию для строк выдачи ДЗ всем уже привязанным ученикам.
    """
    title = (data.get("title") or "").strip()
    if not title:
        errors.append(f"{path}: у домашнего задания должно быть «title».")
        return None

    answer_type = _import_answer_type(data.get("answer_type"), "answer_type", path, errors)
    deadline = _import_dt(data.get("deadline"), "deadline", path, errors)
    auto_issue = bool(data.get("auto_issue"))
    issued = bool(data.get("issued"))

    homework = Homework(
        course=course,
        lesson=lesson,
        title=title[:200],
        task=(data.get("task") or "").strip(),
        answer_type=answer_type,
        expected_answer=str(data.get("expected_answer") or "").strip(),
        numeric_tolerance=float(data.get("numeric_tolerance") or 0),
        require_photo=bool(data.get("require_photo")),
    )
    try:
        homework.full_clean(exclude=["task_image", "created_at"])
    except Exception as exc:  # noqa: BLE001 — показываем текст валидации пользователю
        errors.append(f"{path}: {exc}")
        return None
    homework.save()
    # FIX: настройки выдачи — индивидуальны для каждого ученика (HomeworkIssue).
    _sync_issues_for_homework(homework)
    if auto_issue or issued or deadline:
        HomeworkIssue.objects.filter(homework=homework).update(
            auto_issue=auto_issue,
            issued=issued,
            issued_at=timezone.now() if issued else None,
            deadline=deadline,
        )

    problems = data.get("problems") or []
    if not isinstance(problems, list):
        errors.append(f"{path}: «problems» должен быть списком.")
        problems = []
    for idx, raw in enumerate(problems, start=1):
        ppath = f"{path}, задача {idx}"
        if not isinstance(raw, dict):
            errors.append(f"{ppath}: элемент списка должен быть объектом.")
            continue
        p_type = _import_answer_type(raw.get("answer_type"), "answer_type", ppath, errors)
        problem = HomeworkProblem(
            homework=homework,
            order=int(raw.get("order") or idx),
            text=(raw.get("text") or "").strip(),
            answer_type=p_type,
            expected_answer=str(raw.get("expected_answer") or "").strip(),
            numeric_tolerance=float(raw.get("numeric_tolerance") or 0),
        )
        try:
            problem.full_clean(exclude=["image"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{ppath}: {exc}")
            continue
        problem.save()
    return homework


def _import_lesson(course, data, order, errors):
    """Создаёт Lesson (+ ДЗ из «homeworks») из словаря JSON. Возвращает объект."""
    path = f"Урок {order}"
    title = (data.get("title") or "").strip()
    if not title:
        errors.append(f"{path}: у урока должно быть «title».")
        return None
    lesson = Lesson(
        course=course,
        title=title[:200],
        content=(data.get("content") or "").strip(),
        order=order,
    )
    try:
        lesson.full_clean()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{path}: {exc}")
        return None
    lesson.save()

    homeworks = data.get("homeworks") or data.get("homework") or []
    if isinstance(homeworks, dict):
        homeworks = [homeworks]
    if not isinstance(homeworks, list):
        errors.append(f"{path}: «homeworks» должен быть списком.")
        homeworks = []
    for h_idx, hw in enumerate(homeworks, start=1):
        if not isinstance(hw, dict):
            errors.append(f"{path}, ДЗ {h_idx}: элемент списка должен быть объектом.")
            continue
        _import_homework(course, hw, f"{path}, ДЗ {h_idx}", errors, lesson=lesson)
    return lesson


@teacher_required
def course_import(request, pk):
    """Импорт уроков и домашних заданий курса из JSON."""
    course = get_object_or_404(Course, pk=pk, teacher=request.user)
    errors, created = [], []

    if request.method == "POST":
        raw = ""
        upload = request.FILES.get("json_file")
        if upload:
            try:
                raw = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                errors.append("Файл должен быть в кодировке UTF-8.")
        else:
            raw = (request.POST.get("json_text") or "").strip()

        data = None
        if raw and not errors:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"Некорректный JSON: {exc}.")

        if data is not None:
            if isinstance(data, list):
                payload = {"lessons": data}
            elif isinstance(data, dict):
                payload = data
            else:
                payload = None
                errors.append("JSON должен быть объектом (с «lessons»/«homeworks») или списком уроков.")

            if payload is not None:
                lessons = payload.get("lessons") or []
                if not isinstance(lessons, list):
                    errors.append("«lessons» должен быть списком.")
                    lessons = []
                standalone = payload.get("homeworks") or payload.get("homework") or []
                if isinstance(standalone, dict):
                    standalone = [standalone]
                if not isinstance(standalone, list):
                    errors.append("«homeworks» должен быть списком.")
                    standalone = []

                with transaction.atomic():
                    local_errors = []
                    next_order = course.lessons.count()
                    for l_idx, lesson_data in enumerate(lessons, start=1):
                        if not isinstance(lesson_data, dict):
                            local_errors.append(f"Урок {l_idx}: элемент списка должен быть объектом.")
                            continue
                        obj = _import_lesson(course, lesson_data, next_order + l_idx, local_errors)
                        if obj:
                            created.append(obj)
                    for h_idx, hw in enumerate(standalone, start=1):
                        if not isinstance(hw, dict):
                            local_errors.append(f"ДЗ {h_idx}: элемент списка должен быть объектом.")
                            continue
                        obj = _import_homework(course, hw, f"ДЗ {h_idx}", local_errors)
                        if obj:
                            created.append(obj)
                    if local_errors:
                        # FIX: при любой ошибке не сохраняем ничего — откат транзакции.
                        errors.extend(local_errors)
                        transaction.set_rollback(True)
                        created = []
                    elif created:
                        messages.success(
                            request,
                            f"Импортировано объектов: {len(created)}.",
                        )
                        return redirect(course.get_absolute_url())
                    else:
                        errors.append("В JSON нет ни уроков, ни домашних заданий — импортировать нечего.")

    return render(
        request,
        "lms/course_import.html",
        {"course": course, "errors": errors, "has_problems": bool(errors)},
    )


# --------------------------------------------------------------------------- #
#  Ответы на ДЗ
# --------------------------------------------------------------------------- #
@student_required
def submission_create(request, homework_pk):
    homework = get_object_or_404(Homework.objects.select_related("course"), pk=homework_pk)
    course = homework.course

    if not Enrollment.objects.filter(student=request.user, course=course).exists():
        messages.error(request, "Сначала запишитесь на курс.")
        return redirect(course.get_absolute_url())

    # FIX: дедлайн теперь индивидуальный — берём из строки выдачи ученика.
    my_issue = HomeworkIssue.objects.filter(
        homework=homework, enrollment__student=request.user
    ).first()
    if my_issue is not None and not my_issue.is_available:
        messages.error(request, "Задание ещё не выдано.")
        return redirect(course.get_absolute_url())
    if my_issue and my_issue.deadline and timezone.now() > my_issue.deadline:
        messages.error(request, "Срок сдачи задания истёк.")
        return redirect(homework.get_absolute_url())

    submission = Submission.objects.filter(homework=homework, student=request.user).first()
    if submission and submission.status == Submission.Status.CHECKED:
        messages.info(request, "Ответ уже проверен преподавателем.")
        return redirect(homework.get_absolute_url())

    if request.method == "POST":
        form = SubmissionForm(request.POST, request.FILES, instance=submission, homework=homework)
        if form.is_valid():
            obj = form.save(commit=False)
            # FIX: уникальная отправка обновляется атомарно, что защищает от двойного POST.
            defaults = {
                "answer": obj.answer,
                "file": obj.file,
                "status": Submission.Status.NEW,
            }
            # FIX: ответы на задачи ДЗ из формы form-полей answer_<id>.
            problem_answers = {
                int(name.rsplit("_", 1)[1]): (form.cleaned_data.get(name) or "").strip()
                for name in getattr(form, "problem_fields", [])
            }
            # Автопроверка: если задание настроено — сразу ставим оценку.
            if homework.has_problems:
                is_correct, grade, comment = grade_multi(homework, problem_answers)
                if grade is not None:
                    defaults["grade"] = grade
                    defaults["status"] = Submission.Status.CHECKED
                    defaults["checked_at"] = timezone.now()
                    defaults["teacher_comment"] = comment
                    if is_correct:
                        messages.success(request, "Ответы отправлены. Автопроверка: всё верно.")
                    else:
                        messages.warning(request, f"Ответы отправлены. Автопроверка: {comment}")
                else:
                    messages.info(request, "Автопроверка не настроена — ответы уйдут на ручную проверку.")
            elif homework.is_autograded:
                is_correct, grade, comment = homework.autograde(obj.answer)
                if grade is not None:
                    defaults["grade"] = grade
                    defaults["status"] = Submission.Status.CHECKED
                    defaults["checked_at"] = timezone.now()
                    defaults["teacher_comment"] = comment
                    if is_correct:
                        messages.success(request, "Ответ отправлен. Автопроверка: верный ответ.")
                    else:
                        messages.warning(request, "Ответ отправлен. Автопроверка: неверный ответ.")
                else:
                    messages.info(request, "Автопроверка не настроена — ответ уйдёт на ручную проверку.")
            else:
                messages.success(request, "Ответ отправлен на проверку.")
            obj = Submission.objects.update_or_create(
                homework=homework,
                student=request.user,
                defaults=defaults,
            )[0]
            # FIX: сохраняем построчные ответы на задачи.
            if problem_answers:
                obj.answers.all().delete()
                SubmissionAnswer.objects.bulk_create(
                    SubmissionAnswer(submission=obj, problem_id=pid, answer=ans)
                    for pid, ans in problem_answers.items()
                    if ans
                )
            return redirect(homework.get_absolute_url())
    else:
        form = SubmissionForm(instance=submission, homework=homework, submission=submission)

    return render(
        request,
        "lms/submission_form.html",
        {"form": form, "homework": homework, "course": course, "submission": submission},
    )


@teacher_required
def submission_check(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related("student", "homework", "homework__course"),
        pk=pk,
        homework__course__teacher=request.user,
    )
    if request.method == "POST":
        form = GradeForm(request.POST, instance=submission)
        if form.is_valid():
            obj = form.save(commit=False)
            if obj.status == Submission.Status.NEW:
                obj.status = Submission.Status.CHECKED
            obj.checked_at = timezone.now()
            obj.save()
            messages.success(request, "Ответ проверен.")
            return redirect(submission.homework.get_absolute_url())
    else:
        form = GradeForm(instance=submission)
    return render(request, "lms/submission_check.html", {"form": form, "submission": submission})


@login_required
def my_submissions(request):
    """Список ответов ученика."""
    if not request.user.is_student:
        return redirect("dashboard")
    submissions = Submission.objects.filter(student=request.user).select_related(
        "homework", "homework__course"
    )
    return render(request, "lms/my_submissions.html", {"submissions": submissions})


@teacher_required
def submission_list(request):
    """Список всех ответов преподавателя для проверки."""
    submissions = Submission.objects.filter(
        homework__course__teacher=request.user
    ).select_related("student", "homework", "homework__course")

    status = request.GET.get("status")
    if status:
        submissions = submissions.filter(status=status)

    context = {
        "submissions": submissions,
        "statuses": Submission.Status.choices,
        "current_status": status,
    }
    return render(request, "lms/submission_list.html", context)
@user_passes_test(lambda u: u.is_superuser)
def settings(request):
    """Страница «Настройки», подраздел «Темы оформления» (только суперпользователь)."""
    editing = None
    edit_id = request.GET.get("edit")
    if edit_id:
        editing = get_object_or_404(Theme, pk=edit_id)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete":
            theme_id = request.POST.get("theme_id")
            Theme.objects.filter(pk=theme_id).delete()
            messages.success(request, "Тема удалена.")
            return redirect("settings")

        if action == "activate":
            theme_id = request.POST.get("theme_id")
            Theme.objects.update(is_active=False)
            Theme.objects.filter(pk=theme_id).update(is_active=True)
            messages.success(request, "Тема активирована.")
            return redirect("settings")

        # создание / редактирование
        theme_id = request.POST.get("theme_id")
        instance = Theme.objects.filter(pk=theme_id).first() if theme_id else None
        form = ThemeForm(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            # Только одна тема может быть активной
            if obj.is_active:
                Theme.objects.exclude(pk=obj.pk).update(is_active=False)
            obj.save()
            messages.success(request, "Тема сохранена.")
            return redirect("settings")
        # FIX: при ошибке валидации остаёмся в режиме редактирования конкретной темы.
        if instance:
            editing = instance
    else:
        form = ThemeForm(instance=editing)

    context = {
        "section": "themes",
        "themes": Theme.objects.all(),
        "form": form,
        "editing": editing,
        "font_choices": FONT_CHOICES,
    }
    return render(request, "lms/settings.html", context)


@user_passes_test(lambda u: u.is_superuser)
def settings_json(request):
    """Подраздел «Настроек»: документация по JSON-формату импорта уроков и ДЗ."""
    return render(request, "lms/settings_json.html", {"section": "json"})


# FIX: обратная совместимость старого URL /theme-settings/ — проксируем в настройки.
def theme_settings(request):
    return settings(request)


# --------------------------------------------------------------------------- #
#  Панель учителя: ученики
# --------------------------------------------------------------------------- #
def _managed_student_or_404(request, pk):
    """Ученик доступен учителю, если привязан хотя бы к одному его курсу.

    Суперпользователь-преподаватель видит всех учеников системы.
    """
    student = get_object_or_404(User, pk=pk, role=User.Role.STUDENT)
    teacher = request.user
    if not teacher.is_superuser:
        if not Enrollment.objects.filter(student=student, course__teacher=teacher).exists():
            raise Http404
    return student


@teacher_required
def students_list(request):
    """Список всех учеников на курсах преподавателя с быстрой статистикой."""
    teacher = request.user
    if teacher.is_superuser:
        students = User.objects.filter(role=User.Role.STUDENT)
        submissions_qs = Submission.objects.all()
        dates_qs = LessonDate.objects.all()
    else:
        students = User.objects.filter(
            role=User.Role.STUDENT,
            enrollments__course__teacher=teacher,
        ).distinct()
        submissions_qs = Submission.objects.filter(homework__course__teacher=teacher)
        dates_qs = LessonDate.objects.filter(enrollment__course__teacher=teacher)

    q = request.GET.get("q", "").strip()
    if q:
        students = students.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )
    students = list(students.order_by("first_name", "last_name", "email"))
    ids = [s.pk for s in students]

    stats = {
        row["student"]: row
        for row in submissions_qs.filter(student_id__in=ids)
        .values("student")
        .annotate(
            total=Count("id"),
            new_count=Count("id", filter=Q(status=Submission.Status.NEW)),
            avg_grade=Avg("grade"),
        )
    }
    next_dates = {
        row["enrollment__student"]: row["next_date"]
        for row in dates_qs.filter(
            enrollment__student_id__in=ids, date__gte=timezone.now()
        ).values("enrollment__student").annotate(next_date=Min("date"))
    }

    rows = []
    for s in students:
        st = stats.get(s.pk, {})
        rows.append(
            {
                "student": s,
                "courses_count": s.enrollments.filter(course__teacher=teacher).count()
                if not teacher.is_superuser
                else s.enrollments.count(),
                "total": st.get("total", 0),
                "new_count": st.get("new_count", 0),
                "avg_grade": st.get("avg_grade"),
                "next_date": next_dates.get(s.pk),
            }
        )

    context = {"rows": rows, "q": q, "total_count": len(rows)}
    return render(request, "lms/students_list.html", context)


@teacher_required
def student_detail(request, pk):
    """Карточка ученика: параметры, курсы, даты занятий, сданные работы."""
    student = _managed_student_or_404(request, pk)
    teacher = request.user

    enrollments = student.enrollments.select_related("course").order_by("-enrolled_at")
    submissions = (
        Submission.objects.filter(student=student)
        .select_related("homework", "homework__course")
        .order_by("-submitted_at")
    )
    if not teacher.is_superuser:
        enrollments = enrollments.filter(course__teacher=teacher)
        submissions = submissions.filter(homework__course__teacher=teacher)

    checked = [s for s in submissions if s.grade is not None]
    avg_grade = round(sum(s.grade for s in checked) / len(checked), 1) if checked else None
    upcoming = (
        LessonDate.objects.filter(enrollment__student=student, date__gte=timezone.now())
        .select_related("lesson", "enrollment__course")
        .order_by("date")[:10]
    )
    teacher_courses = Course.objects.filter(teacher=teacher).exclude(
        pk__in=enrollments.values_list("course_id", flat=True)
    )

    context = {
        "student": student,
        "enrollments": enrollments,
        "submissions": submissions,
        "submissions_count": submissions.count(),
        "new_count": submissions.filter(status=Submission.Status.NEW).count(),
        "avg_grade": avg_grade,
        "upcoming": upcoming,
        "teacher_courses": teacher_courses,
    }
    return render(request, "lms/student_detail.html", context)


@teacher_required
def student_edit(request, pk):
    """Редактирование всех параметров ученика (ФИО, email, био, аватар,
    активность, сброс пароля)."""
    student = _managed_student_or_404(request, pk)

    if request.method == "POST":
        form = StudentManageForm(request.POST, request.FILES, instance=student)
        if form.is_valid():
            user = form.save()
            new_password = form.cleaned_data.get("new_password")
            if new_password:
                user.set_password(new_password)
                user.save(update_fields=["password"])
                messages.success(request, "Пароль ученика сброшен.")
            messages.success(request, "Данные ученика сохранены.")
            return redirect("student_detail", pk=student.pk)
    else:
        form = StudentManageForm(instance=student)

    context = {"form": form, "student": student, "title": "Редактирование ученика"}
    return render(request, "lms/student_edit.html", context)


@teacher_required
def student_enroll_course(request, pk):
    """Преподаватель привязывает ученика к своему курсу из панели."""
    student = _managed_student_or_404(request, pk)
    if request.method == "POST":
        course_id = request.POST.get("course_id")
        course = get_object_or_404(Course, pk=course_id, teacher=request.user)
        enrollment, created = Enrollment.objects.get_or_create(student=student, course=course)
        if created:
            _sync_issues_for_enrollment(enrollment)
            messages.success(request, f"{student.full_name} добавлен(а) на курс «{course.title}».")
        else:
            messages.info(request, "Этот ученик уже привязан к курсу.")
    return redirect("student_detail", pk=student.pk)


@teacher_required
def student_unenroll_course(request, pk, enrollment_pk):
    """Преподаватель отвязывает ученика от своего курса из панели."""
    student = _managed_student_or_404(request, pk)
    enrollment = get_object_or_404(
        Enrollment, pk=enrollment_pk, student=student, course__teacher=request.user
    )
    if request.method == "POST":
        course_title = enrollment.course.title
        enrollment.delete()
        messages.success(request, f"Ученик отвязан от курса «{course_title}».")
    return redirect("student_detail", pk=student.pk)