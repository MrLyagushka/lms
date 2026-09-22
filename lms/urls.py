from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # Публичные страницы
    path("", views.landing, name="landing"),
    path("register/", views.register, name="register"),

    # Auth
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="lms/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Dashboard / profile
    path("dashboard/", views.dashboard, name="dashboard"),
    path("profile/", views.profile, name="profile"),

    # Кастомные темы (суперпользователь)
    path("theme-settings/", views.theme_settings, name="theme_settings"),

    # Courses
    path("courses/", views.course_list, name="course_list"),
    path("courses/new/", views.course_create, name="course_create"),
    path("courses/<int:pk>/", views.course_detail, name="course_detail"),
    path("courses/<int:pk>/edit/", views.course_edit, name="course_edit"),
    path("courses/<int:pk>/delete/", views.course_delete, name="course_delete"),
    path("courses/<int:pk>/enroll/", views.enroll, name="enroll"),
    path("courses/<int:pk>/unenroll/", views.unenroll, name="unenroll"),

    # Lessons
    path("lessons/<int:pk>/", views.lesson_detail, name="lesson_detail"),
    path("courses/<int:course_pk>/lessons/new/", views.lesson_create, name="lesson_create"),
    path("lessons/<int:pk>/edit/", views.lesson_edit, name="lesson_edit"),
    path("lessons/<int:pk>/delete/", views.lesson_delete, name="lesson_delete"),

    # Homework
    path("homework/<int:pk>/", views.homework_detail, name="homework_detail"),
    path("courses/<int:course_pk>/homework/new/", views.homework_create, name="homework_create"),
    path("homework/<int:pk>/edit/", views.homework_edit, name="homework_edit"),
    path("homework/<int:pk>/delete/", views.homework_delete, name="homework_delete"),

    # Submissions
    path("homework/<int:homework_pk>/submit/", views.submission_create, name="submission_create"),
    path("submissions/", views.submission_list, name="submission_list"),
    path("submissions/mine/", views.my_submissions, name="my_submissions"),
    path("submissions/<int:pk>/check/", views.submission_check, name="submission_check"),
]