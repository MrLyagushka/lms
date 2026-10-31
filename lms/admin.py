from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Course,
    CustomUser,
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


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "subject", "grade_level", "teacher", "is_published", "students_count", "lessons_count", "created_at")
    list_filter = ("subject", "grade_level", "is_published", "created_at")
    list_editable = ("is_published",)
    search_fields = ("title", "description")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("teacher",)

    @admin.display(description="Учеников")
    def students_count(self, obj):
        return obj.students_count

    @admin.display(description="Уроков")
    def lessons_count(self, obj):
        return obj.lessons_count


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "enrolled_at")
    list_filter = ("course", "enrolled_at")
    search_fields = ("student__email", "course__title")
    autocomplete_fields = ("student", "course")


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    # FIX: у занятия нет общей даты/отметки — всё индивидуально для каждого ученика.
    list_display = ("title", "course", "order", "created_at")
    list_filter = ("course",)
    search_fields = ("title", "content")
    autocomplete_fields = ("course",)


@admin.register(LessonDate)
class LessonDateAdmin(admin.ModelAdmin):
    list_display = ("student", "lesson", "course", "date", "is_conducted", "conducted_at")
    list_filter = ("lesson__course", "is_conducted")
    search_fields = ("enrollment__student__email", "lesson__title")
    autocomplete_fields = ("enrollment", "lesson")

    @admin.display(description="Ученик")
    def student(self, obj):
        return obj.enrollment.student

    @admin.display(description="Курс")
    def course(self, obj):
        return obj.enrollment.course


class HomeworkProblemInline(admin.TabularInline):
    model = HomeworkProblem
    extra = 0


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    # FIX: выдача и дедлайны — в HomeworkIssue (индивидуально для ученика).
    list_display = ("title", "course", "lesson", "answer_type", "require_photo", "created_at")
    list_filter = ("course", "answer_type", "require_photo")
    search_fields = ("title", "task")
    autocomplete_fields = ("course", "lesson")
    inlines = [HomeworkProblemInline]


@admin.register(HomeworkIssue)
class HomeworkIssueAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "homework", "auto_issue", "issued", "issued_at", "deadline")
    list_filter = ("enrollment__course", "auto_issue", "issued")
    search_fields = ("enrollment__student__email", "homework__title")
    autocomplete_fields = ("enrollment", "homework")

    @admin.display(description="Ученик")
    def student(self, obj):
        return obj.enrollment.student

    @admin.display(description="Курс")
    def course(self, obj):
        return obj.enrollment.course


@admin.register(HomeworkProblem)
class HomeworkProblemAdmin(admin.ModelAdmin):
    list_display = ("homework", "order", "answer_type", "expected_answer")
    list_filter = ("answer_type", "homework__course")
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
