from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .autograding import grade_multi
from .decorators import student_required, teacher_required
from .forms import (
    FONT_CHOICES,
    GradeForm,
    HomeworkForm,
    HomeworkIssueStudentsForm,
    HomeworkProblemFormSet,
    LinkStudentForm,
    ProfileForm,
    StudentManageForm,
    SubmissionForm,
    ThemeForm,
    UserRegistrationForm,
)
from .models import (
    Homework,
    HomeworkIssue,
    HomeworkProblem,
    StudentTeacherLink,
    Submission,
    SubmissionAnswer,
    Theme,
)

User = get_user_model()


def _student_teacher(user):
    """Учитель, к которому привязан ученик; None, если привязки нет.

    FIX: OneToOneField-дескриптор бросает RelatedObjectDoesNotExist при
    обращении к отсутствующей связанной записи (getattr с default здесь
    не спасает), что давало 500 на /dashboard/ для пользователей без
    StudentTeacherLink (например, суперпользователь).
    """
    try:
        return user.teacher_link.teacher
    except StudentTeacherLink.DoesNotExist:
        return None


def _homeworks_of_teacher(teacher):
    """Все ДЗ учителя (по строкам выдачи ученикам)."""
    if teacher.is_superuser:
        return Homework.objects.all()
    return Homework.objects.filter(issues__teacher=teacher).distinct()


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
            issue = HomeworkIssue(homework=homework, student=student, teacher=teacher)
        if not issue.issued:
            issue.issued_at = now
        issue.issued = True
        if teacher and not issue.teacher_id:
            issue.teacher = teacher
        if deadline is not None:
            issue.deadline = deadline
        to_update.append(issue)
    HomeworkIssue.objects.bulk_create(to_update, update_conflicts=True,
                                      unique_fields=["homework", "student"],
                                      update_fields=["issued", "issued_at", "deadline", "teacher"])
    return len(to_update)


# --------------------------------------------------------------------------- #
#  Публичные страницы
# --------------------------------------------------------------------------- #
def landing(request):
    """Рекламный лендинг — доступен без авторизации."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "lms/landing.html", {})


@require_GET
def secure_media(request, name):
    """Отдаёт media только пользователю, которому разрешен доступ к объекту."""
    # FIX: media больше не раздается Caddy напрямую, иначе URL файла обходил ACL.
    if not request.user.is_authenticated or not name or ".." in name or "\\" in name:
        raise Http404

    submission = Submission.objects.select_related(
        "student", "homework__issues__teacher"
    ).filter(file=name).first()
    if submission:
        allowed = (
            request.user.is_superuser
            or request.user == submission.student
            or submission.homework.teacher == request.user
        )
    else:
        avatar = User.objects.filter(avatar=name).first()
        allowed = bool(avatar) and request.user.is_authenticated

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
            Submission.objects.filter(homework__issues__teacher=request.user)
            .select_related("student", "homework")
            .distinct()
        )
        context = {
            "students_count": StudentTeacherLink.objects.filter(
                teacher=request.user
            ).count(),
            "homework_count": _homeworks_of_teacher(request.user).count(),
            "homeworks": _homeworks_of_teacher(request.user).order_by("-created_at")[:6],
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
    submissions = Submission.objects.filter(student=request.user).select_related("homework")
    context = {
        # FIX: RelatedObjectDoesNotExist — у пользователя может не быть записи
        # StudentTeacherLink (например, учитель с ролью STUDENT), поэтому
        # используем явный try/except (_student_teacher).
        "teacher": _student_teacher(request.user),
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
#  Домашние задания
# --------------------------------------------------------------------------- #
@login_required
def homework_detail(request, pk):
    """Страница ДЗ: владелец (учитель) видит все ответы, ученик — свой."""
    homework = get_object_or_404(Homework, pk=pk)

    my_issue = HomeworkIssue.objects.filter(
        homework=homework, student=request.user
    ).select_related("teacher").first()
    is_owner = (not request.user.is_student and
                (request.user.is_superuser or my_issue is None or my_issue.teacher_id == request.user.pk))
    if not is_owner and my_issue is None:
        # Ученик без выдачи — доступ нет.
        messages.error(request, "Нет доступа к этому заданию.")
        return redirect("dashboard")

    # FIX: выдача ДЗ индивидуальна — проверка по строке HomeworkIssue ученика.
    if request.user.is_student and my_issue is not None and not my_issue.is_available:
        messages.info(request, "Задание ещё не выдано учителем.")
        return redirect("dashboard")

    teacher = my_issue.teacher if my_issue else homework.teacher
    my_submission = None
    submissions = None
    my_answers = {}
    if is_owner:
        submissions = homework.submissions.select_related("student")
    else:
        my_submission = homework.submissions.filter(student=request.user).first()
        if my_submission:
            my_answers = {a.problem_id: a.answer for a in my_submission.answers.all()}

    context = {
        "homework": homework,
        "teacher": teacher,
        "is_owner": is_owner,
        "my_submission": my_submission,
        "my_answers": my_answers,
        "submissions": submissions,
        "my_issue": my_issue,
        "now": timezone.now(),
    }
    return render(request, "lms/homework_detail.html", context)


def _owned_homework_or_404(request, pk):
    """ДЗ доступно учителю, который выдавал его хоть одному ученику (или суперадмину)."""
    homework = get_object_or_404(Homework, pk=pk)
    if request.user.is_superuser:
        return homework
    if not HomeworkIssue.objects.filter(homework=homework, teacher=request.user).exists():
        raise Http404
    return homework


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
def homework_create(request):
    """Создание ДЗ учителем и выдача его выбранным привязанным ученикам."""
    if request.method == "POST":
        form = HomeworkForm(request.POST)
        formset = HomeworkProblemFormSet(request.POST, request.FILES, prefix="problems")
        issue_form = HomeworkIssueStudentsForm(request.POST, teacher=request.user)
        if form.is_valid() and formset.is_valid() and issue_form.is_valid():
            homework = form.save()
            formset.instance = homework
            formset.save()
            _renumber_problems(homework)
            students = list(issue_form.cleaned_data["students"])
            issued = _issue_homework_to_students(
                homework, students, deadline=issue_form.cleaned_data.get("deadline"),
                teacher=request.user,
            )
            messages.success(request, f"Домашнее задание создано и выдано ({issued} уч.).")
            return redirect(homework.get_absolute_url())
    else:
        form = HomeworkForm()
        formset = HomeworkProblemFormSet(prefix="problems")
        issue_form = HomeworkIssueStudentsForm(teacher=request.user)
    return render(
        request,
        "lms/homework_form.html",
        {
            "form": form,
            "formset": formset,
            "issue_form": issue_form,
            "title": "Новое домашнее задание",
        },
    )


@teacher_required
def homework_edit(request, pk):
    homework = _owned_homework_or_404(request, pk)
    if request.method == "POST":
        form = HomeworkForm(request.POST, instance=homework)
        formset = HomeworkProblemFormSet(request.POST, request.FILES, instance=homework, prefix="problems")
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            _renumber_problems(homework)
            messages.success(request, "Домашнее задание обновлено.")
            return redirect(homework.get_absolute_url())
    else:
        form = HomeworkForm(instance=homework)
        formset = HomeworkProblemFormSet(instance=homework, prefix="problems")
    return render(
        request,
        "lms/homework_form.html",
        {
            "form": form,
            "formset": formset,
            "homework": homework,
            "title": "Редактирование ДЗ",
        },
    )


@teacher_required
def homework_delete(request, pk):
    homework = _owned_homework_or_404(request, pk)
    if request.method == "POST":
        homework.delete()
        messages.success(request, "Домашнее задание удалено.")
        return redirect("dashboard")
    return render(
        request,
        "lms/confirm_delete.html",
        {"object": homework, "cancel_url": homework.get_absolute_url()},
    )


@student_required
def submission_create(request, homework_pk):
    """Отправка/обновление ответа ученика на выданное ему ДЗ."""
    homework = get_object_or_404(Homework, pk=homework_pk)

    # FIX: доступ к сдаче — по строке выдачи HomeworkIssue (привязка ученик ↔ учитель).
    my_issue = HomeworkIssue.objects.filter(
        homework=homework, student=request.user
    ).select_related("teacher").first()
    if my_issue is None:
        messages.error(request, "Это задание вам ещё не выдавалось.")
        return redirect("dashboard")
    if not my_issue.is_available:
        messages.error(request, "Задание ещё не выдано.")
        return redirect("dashboard")
    if my_issue.deadline and timezone.now() > my_issue.deadline:
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
        {"form": form, "homework": homework, "teacher": my_issue.teacher, "submission": submission},
    )


@teacher_required
def submission_check(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related("student", "homework"),
        pk=pk,
        homework__issues__teacher=request.user,
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
    submissions = Submission.objects.filter(student=request.user).select_related("homework")
    return render(request, "lms/my_submissions.html", {"submissions": submissions})


@teacher_required
def submission_list(request):
    """Список всех ответов преподавателя для проверки."""
    submissions = Submission.objects.filter(
        homework__issues__teacher=request.user
    ).select_related("student", "homework").distinct()

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


# --------------------------------------------------------------------------- #
#  Панель учителя: ученики
# --------------------------------------------------------------------------- #
def _managed_student_or_404(request, pk):
    """Ученик доступен учителю, если привязан к нему (StudentTeacherLink).

    Суперпользователь-преподаватель видит всех учеников системы.
    """
    student = get_object_or_404(User, pk=pk, role=User.Role.STUDENT)
    teacher = request.user
    if not teacher.is_superuser:
        if not StudentTeacherLink.objects.filter(student=student, teacher=teacher).exists():
            raise Http404
    return student


@teacher_required
def students_list(request):
    """Список учеников, привязанных к преподавателю, с быстрой статистикой."""
    teacher = request.user
    if teacher.is_superuser:
        students = User.objects.filter(role=User.Role.STUDENT)
        submissions_qs = Submission.objects.all()
        issues_qs = HomeworkIssue.objects.all()
    else:
        students = User.objects.filter(
            role=User.Role.STUDENT,
            teacher_link__teacher=teacher,
        )
        submissions_qs = Submission.objects.filter(homework__issues__teacher=teacher)
        issues_qs = HomeworkIssue.objects.filter(teacher=teacher)

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
    issued_counts = {
        row["student"]: row["issued"]
        for row in issues_qs.filter(
            student_id__in=ids, issued=True
        ).values("student").annotate(issued=Count("homework", distinct=True))
    }

    rows = []
    for s in students:
        st = stats.get(s.pk, {})
        rows.append(
            {
                "student": s,
                "issued_count": issued_counts.get(s.pk, 0),
                "total": st.get("total", 0),
                "new_count": st.get("new_count", 0),
                "avg_grade": st.get("avg_grade"),
            }
        )

    context = {"rows": rows, "q": q, "total_count": len(rows)}
    return render(request, "lms/students_list.html", context)


@teacher_required
def student_detail(request, pk):
    """Карточка ученика: выданные ДЗ и сданные работы."""
    student = _managed_student_or_404(request, pk)
    teacher = request.user

    issues = HomeworkIssue.objects.filter(student=student, issued=True).select_related(
        "homework", "teacher"
    )
    submissions = (
        Submission.objects.filter(student=student)
        .select_related("homework")
        .order_by("-submitted_at")
    )
    if not teacher.is_superuser:
        issues = issues.filter(teacher=teacher)
        submissions = submissions.filter(homework__issues__teacher=teacher)

    checked = [s for s in submissions if s.grade is not None]
    avg_grade = round(sum(s.grade for s in checked) / len(checked), 1) if checked else None

    context = {
        "student": student,
        "issues": issues.order_by("-issued_at")[:20],
        "submissions": submissions,
        "submissions_count": submissions.count(),
        "new_count": submissions.filter(status=Submission.Status.NEW).count(),
        "avg_grade": avg_grade,
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
