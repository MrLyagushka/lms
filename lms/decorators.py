from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def teacher_required(view_func):
    """Доступ только для преподавателей."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_teacher:
            messages.error(request, "Доступно только преподавателям.")
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)

    return _wrapped


def student_required(view_func):
    """Доступ только для учеников."""

    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_student:
            messages.error(request, "Доступно только ученикам.")
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)

    return _wrapped