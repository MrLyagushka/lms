from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .decorators import student_required, teacher_required
from .forms import (
    CourseForm,
    GradeForm,
    HomeworkForm,
    LessonForm,
    ProfileForm,
    SubmissionForm,
    ThemeForm,
    UserRegistrationForm,
)
from .models import Course, Enrollment, Homework, Lesson, Submission, Theme

User = get_user_model()


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
        courses = (
            Course.objects.filter(teacher=request.user)
            .annotate(num_students=Count("enrollments"), num_lessons=Count("lessons"))
        )
        submissions = (
            Submission.objects.filter(homework__course__teacher=request.user)
            .select_related("student", "homework")
        )
        context = {
            "courses": courses,
            "courses_count": courses.count(),
            "students_count": Enrollment.objects.filter(
                course__teacher=request.user
            ).values("student").distinct().count(),
            "homework_count": Homework.objects.filter(
                course__teacher=request.user
            ).count(),
            "new_submissions_count": submissions.filter(
                status=Submission.Status.NEW
            ).count(),
            "recent_submissions": submissions[:5],
        }
        return render(request, "lms/dashboard_teacher.html", context)

    # Ученик
    courses = Course.objects.filter(enrollments__student=request.user)
    submissions = Submission.objects.filter(student=request.user).select_related(
        "homework", "homework__course"
    )
    context = {
        "courses": courses,
        "courses_count": courses.count(),
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

    subject = request.GET.get("subject")
    if subject:
        courses = courses.filter(subject=subject)

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
        "current_subject": subject,
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

    # Доступ к содержанию: преподаватель-владелец либо записанный ученик
    can_view_content = is_owner or is_enrolled

    context = {
        "course": course,
        "lessons": course.lessons.all() if can_view_content else [],
        "homeworks": course.homeworks.all() if can_view_content else [],
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
    course = get_object_or_404(Course, pk=pk)
    Enrollment.objects.get_or_create(student=request.user, course=course)
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

    context = {
        "lesson": lesson,
        "course": course,
        "is_owner": is_owner,
        "homeworks": lesson.homeworks.all(),
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

    my_submission = None
    submissions = None
    if is_owner:
        submissions = homework.submissions.select_related("student")
    elif is_enrolled:
        my_submission = homework.submissions.filter(student=request.user).first()

    context = {
        "homework": homework,
        "course": course,
        "is_owner": is_owner,
        "my_submission": my_submission,
        "submissions": submissions,
        "now": timezone.now(),
    }
    return render(request, "lms/homework_detail.html", context)


@teacher_required
def homework_create(request, course_pk):
    course = get_object_or_404(Course, pk=course_pk, teacher=request.user)
    if request.method == "POST":
        form = HomeworkForm(request.POST, course=course)
        if form.is_valid():
            homework = form.save(commit=False)
            homework.course = course
            homework.save()
            messages.success(request, "Домашнее задание создано.")
            return redirect(homework.get_absolute_url())
    else:
        form = HomeworkForm(course=course)
    return render(
        request,
        "lms/homework_form.html",
        {"form": form, "course": course, "title": "Новое домашнее задание"},
    )


@teacher_required
def homework_edit(request, pk):
    homework = get_object_or_404(Homework, pk=pk, course__teacher=request.user)
    if request.method == "POST":
        form = HomeworkForm(request.POST, instance=homework, course=homework.course)
        if form.is_valid():
            form.save()
            messages.success(request, "Домашнее задание обновлено.")
            return redirect(homework.get_absolute_url())
    else:
        form = HomeworkForm(instance=homework, course=homework.course)
    return render(
        request,
        "lms/homework_form.html",
        {"form": form, "course": homework.course, "homework": homework, "title": "Редактирование ДЗ"},
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
#  Ответы на ДЗ
# --------------------------------------------------------------------------- #
@student_required
def submission_create(request, homework_pk):
    homework = get_object_or_404(Homework.objects.select_related("course"), pk=homework_pk)
    course = homework.course

    if not Enrollment.objects.filter(student=request.user, course=course).exists():
        messages.error(request, "Сначала запишитесь на курс.")
        return redirect(course.get_absolute_url())

    if homework.deadline and timezone.now() > homework.deadline:
        messages.error(request, "Срок сдачи задания истёк.")
        return redirect(homework.get_absolute_url())

    submission = Submission.objects.filter(homework=homework, student=request.user).first()
    if submission and submission.status == Submission.Status.CHECKED:
        messages.info(request, "Ответ уже проверен преподавателем.")
        return redirect(homework.get_absolute_url())

    if request.method == "POST":
        form = SubmissionForm(request.POST, request.FILES, instance=submission)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.homework = homework
            obj.student = request.user
            obj.status = Submission.Status.NEW
            obj.save()
            messages.success(request, "Ответ отправлен на проверку.")
            return redirect(homework.get_absolute_url())
    else:
        form = SubmissionForm(instance=submission)

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
def theme_settings(request):
    """Управление кастомными темами (только суперпользователь)."""
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
            return redirect("theme_settings")

        if action == "activate":
            theme_id = request.POST.get("theme_id")
            Theme.objects.update(is_active=False)
            Theme.objects.filter(pk=theme_id).update(is_active=True)
            messages.success(request, "Тема активирована.")
            return redirect("theme_settings")

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
            return redirect("theme_settings")
    else:
        form = ThemeForm(instance=editing)

    context = {
        "themes": Theme.objects.all(),
        "form": form,
        "editing": editing,
    }
    return render(request, "lms/theme_settings.html", context)