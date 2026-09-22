from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.urls import reverse


class CustomUserManager(BaseUserManager):
    """Менеджер пользователей с email вместо username."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email обязателен")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Суперпользователь должен иметь is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Суперпользователь должен иметь is_superuser=True")
        return self._create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    """Пользователь системы: преподаватель или ученик (вход по email)."""

    class Role(models.TextChoices):
        TEACHER = "TEACHER", "Преподаватель"
        STUDENT = "STUDENT", "Ученик"

    username = None
    email = models.EmailField("Email", unique=True)
    role = models.CharField(
        "Роль",
        max_length=10,
        choices=Role.choices,
        default=Role.STUDENT,
    )
    bio = models.TextField("О себе", blank=True)
    avatar = models.ImageField(
        "Аватар",
        upload_to="avatars/",
        blank=True,
        null=True,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name"]

    objects = CustomUserManager()

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        return f"{self.get_full_name() or self.email} ({self.get_role_display()})"

    @property
    def full_name(self):
        return self.get_full_name() or self.email

    @property
    def is_teacher(self):
        return self.role == self.Role.TEACHER

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT


class Subject(models.TextChoices):
    """Предметы ЕГЭ/ОГЭ."""

    MATH = "MATH", "Математика"
    RUSSIAN = "RUSSIAN", "Русский язык"
    PHYSICS = "PHYSICS", "Физика"
    CHEMISTRY = "CHEMISTRY", "Химия"
    BIOLOGY = "BIOLOGY", "Биология"
    INFORMATICS = "INFORMATICS", "Информатика"
    HISTORY = "HISTORY", "История"
    SOCIAL = "SOCIAL", "Обществознание"
    ENGLISH = "ENGLISH", "Английский язык"
    GEOGRAPHY = "GEOGRAPHY", "География"
    LITERATURE = "LITERATURE", "Литература"
    OTHER = "OTHER", "Другое"


class Course(models.Model):
    """Курс подготовки к экзамену."""

    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("Slug", max_length=220, unique=True, blank=True)
    description = models.TextField("Описание", blank=True)
    subject = models.CharField(
        "Предмет",
        max_length=20,
        choices=Subject.choices,
        default=Subject.MATH,
    )
    is_published = models.BooleanField(
        "Опубликован",
        default=False,
        help_text="Показывать курс на главной странице (лендинге).",
    )
    teacher = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="taught_courses",
        verbose_name="Преподаватель",
        limit_choices_to={"role": CustomUser.Role.TEACHER},
    )
    students = models.ManyToManyField(
        CustomUser,
        through="Enrollment",
        related_name="enrolled_courses",
        verbose_name="Ученики",
        blank=True,
    )
    cover = models.ImageField(
        "Обложка",
        upload_to="covers/",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Курс"
        verbose_name_plural = "Курсы"

    def __str__(self):
        return self.title
    def get_absolute_url(self):
        return reverse("course_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        # Автоматическая генерация уникального slug из названия
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.title) or "course"
            slug = base
            counter = 1
            while Course.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def lessons_count(self):
        return self.lessons.count()

    @property
    def students_count(self):
        return self.enrollments.count()


class Enrollment(models.Model):
    """Запись ученика на курс."""

    student = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Ученик",
        limit_choices_to={"role": CustomUser.Role.STUDENT},
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Курс",
    )
    enrolled_at = models.DateTimeField("Дата записи", auto_now_add=True)

    class Meta:
        unique_together = ("student", "course")
        verbose_name = "Запись на курс"
        verbose_name_plural = "Записи на курсы"

    def __str__(self):
        return f"{self.student} -> {self.course}"


class Lesson(models.Model):
    """Урок внутри курса."""

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="lessons",
        verbose_name="Курс",
    )
    title = models.CharField("Название", max_length=200)
    content = models.TextField("Содержание", blank=True)
    order = models.PositiveIntegerField("Порядковый номер", default=1)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ("order", "id")
        unique_together = ("course", "order")
        verbose_name = "Урок"
        verbose_name_plural = "Уроки"

    def __str__(self):
        return f"{self.order}. {self.title}"

    def get_absolute_url(self):
        return reverse("lesson_detail", args=[self.pk])


class Homework(models.Model):
    """Домашнее задание к уроку."""

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="homeworks",
        verbose_name="Курс",
    )
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name="homeworks",
        verbose_name="Урок",
        null=True,
        blank=True,
    )
    title = models.CharField("Название", max_length=200)
    task = models.TextField("Задание")
    deadline = models.DateTimeField("Дедлайн", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Домашнее задание"
        verbose_name_plural = "Домашние задания"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("homework_detail", args=[self.pk])


class Submission(models.Model):
    """Ответ ученика на домашнее задание."""

    class Status(models.TextChoices):
        NEW = "NEW", "Новое"
        CHECKED = "CHECKED", "Проверено"
        RETURNED = "RETURNED", "На доработку"

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="Домашнее задание",
    )
    student = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="Ученик",
    )
    answer = models.TextField("Ответ ученика", blank=True)
    file = models.FileField(
        "Файл",
        upload_to="submissions/",
        blank=True,
        null=True,
    )
    status = models.CharField(
        "Статус",
        max_length=10,
        choices=Status.choices,
        default=Status.NEW,
    )
    grade = models.PositiveSmallIntegerField(
        "Оценка",
        null=True,
        blank=True,
    )
    teacher_comment = models.TextField("Комментарий преподавателя", blank=True)
    submitted_at = models.DateTimeField("Отправлено", auto_now_add=True)
    checked_at = models.DateTimeField("Проверено", null=True, blank=True)

    class Meta:
        unique_together = ("homework", "student")
        ordering = ("-submitted_at",)
        verbose_name = "Ответ на ДЗ"
        verbose_name_plural = "Ответы на ДЗ"

    def __str__(self):
        return f"{self.student} - {self.homework}"
class Theme(models.Model):
    """Кастомная тема оформления (настраивается суперпользователем)."""

    name = models.CharField("Название", max_length=100)
    is_active = models.BooleanField("Активна", default=False)
    primary_color = models.CharField("Основной цвет", max_length=7, default="#4F46E5")
    secondary_color = models.CharField("Дополнительный цвет", max_length=7, default="#10B981")
    background_color = models.CharField("Цвет фона", max_length=7, default="#FFFFFF")
    text_color = models.CharField("Цвет текста", max_length=7, default="#111827")
    card_background = models.CharField("Фон карточек", max_length=7, default="#FFFFFF")
    navbar_background = models.CharField("Фон навбара", max_length=7, default="#FFFFFF")
    font_family = models.CharField("Шрифт", max_length=100, default="Inter, sans-serif")
    border_radius = models.CharField("Скругление", max_length=20, default="0.5rem")
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Тема"
        verbose_name_plural = "themes"

    def __str__(self):
        return f"{self.name}{' (активна)' if self.is_active else ''}"