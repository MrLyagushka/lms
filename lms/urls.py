from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # Публичные страницы
    path("", views.landing, name="landing"),
    path("register/", views.register, name="register"),
    # FIX: media проходит через проверку владельца/участника в secure_media.
    path("media/<path:name>", views.secure_media, name="secure_media"),

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

    # Настройки (суперпользователь): тема
    path("settings/", views.settings, name="settings"),

    # Homework
    path("homework/<int:pk>/", views.homework_detail, name="homework_detail"),
    path("homework/new/", views.homework_create, name="homework_create"),
    path("homework/<int:pk>/edit/", views.homework_edit, name="homework_edit"),
    path("homework/<int:pk>/delete/", views.homework_delete, name="homework_delete"),

    # Submissions
    path("homework/<int:homework_pk>/submit/", views.submission_create, name="submission_create"),
    path("submissions/", views.submission_list, name="submission_list"),
    path("submissions/mine/", views.my_submissions, name="my_submissions"),
    path("submissions/<int:pk>/check/", views.submission_check, name="submission_check"),

    # Панель учителя: ученики
    path("students/", views.students_list, name="students_list"),
    path("students/<int:pk>/", views.student_detail, name="student_detail"),
    path("students/<int:pk>/edit/", views.student_edit, name="student_edit"),
]