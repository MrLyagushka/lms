from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    CustomUser,
    Homework,
    HomeworkIssue,
    HomeworkProblem,
    StudentTeacherLink,
    Submission,
    SubmissionAnswer,
    Theme,
)


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ("email", "full_name", "role", "is_staff")
    list_filter = ("role", "is_staff", "is_superuser")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Персональные данные", {"fields": ("first_name", "last_name")}),
        ("Профиль LMS", {"fields": ("role", "bio", "avatar")}),
        (
            "Права",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "role", "password1", "password2"),
            },
        ),
    )


@admin.register(StudentTeacherLink)
class StudentTeacherLinkAdmin(admin.ModelAdmin):
    list_display = ("student", "teacher", "created_at")
    list_filter = ("teacher", "created_at")
    search_fields = (
        "student__email", "student__first_name", "student__last_name",
        "teacher__email", "teacher__first_name", "teacher__last_name",
    )


class HomeworkProblemInline(admin.TabularInline):
    model = HomeworkProblem
    extra = 0


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    # NOTE: выдача и дедлайны — в HomeworkIssue (индивидуально для ученика).
    list_display = ("title", "answer_type", "require_photo", "created_at")
    list_filter = ("answer_type", "require_photo")
    search_fields = ("title", "task")
    inlines = [HomeworkProblemInline]


@admin.register(HomeworkIssue)
class HomeworkIssueAdmin(admin.ModelAdmin):
    list_display = ("student", "teacher", "homework", "issued", "issued_at", "deadline")
    list_filter = ("teacher", "issued")
    search_fields = ("student__email", "homework__title")
    autocomplete_fields = ("student", "teacher", "homework")


@admin.register(HomeworkProblem)
class HomeworkProblemAdmin(admin.ModelAdmin):
    list_display = ("homework", "order", "answer_type", "expected_answer")
    list_filter = ("answer_type",)
    search_fields = ("text", "expected_answer", "homework__title")


class SubmissionAnswerInline(admin.TabularInline):
    model = SubmissionAnswer
    extra = 0
    readonly_fields = ("problem",)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("student", "homework", "status", "grade", "submitted_at")
    list_filter = ("status", "submitted_at")
    search_fields = ("student__email", "homework__title", "answer")
    autocomplete_fields = ("homework", "student")
    readonly_fields = ("submitted_at", "checked_at")
    inlines = [SubmissionAnswerInline]


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "primary_color", "font_family", "created_at")
    list_filter = ("is_active",)
    list_editable = ()
    fields = (
        "name",
        "is_active",
        "primary_color",
        "secondary_color",
        "background_color",
        "text_color",
        "card_background",
        "navbar_background",
        "font_family",
        "border_radius",
    )
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        # Только одна тема может быть активной
        if obj.is_active:
            Theme.objects.exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)
