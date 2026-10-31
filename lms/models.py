from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator, RegexValidator
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
        # FIX: ограничиваем загружаемые аватары изображениями.
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])],
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
        # Суперпользователь имеет доступ к панели учителя.
        return self.is_superuser or self.role == self.Role.TEACHER

    @property
    def is_student(self):
        return not self.is_superuser and self.role == self.Role.STUDENT

    def get_role_display(self):
        # Поле role не меняется — миграций это не требует.
        # super().get_role_display() здесь невозможен: метод get_FOO_display
        # генерируется Meta-классом Django в самом классе модели, поэтому
        # обращаемся к choices напрямую.
        if self.is_superuser:
            return "Администратор"
        return dict(self.Role.choices).get(self.role, self.role)


class StudentTeacherLink(models.Model):
    """Привязка ученика к учителю (основная связь в текущей версии).

    ДЗ выдаются учителем напрямую привязанным ученикам.
    """

    student = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="teacher_link",
        verbose_name="Ученик",
        limit_choices_to={"role": CustomUser.Role.STUDENT},
    )
    teacher = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="linked_students",
        verbose_name="Учитель",
        limit_choices_to={"role": CustomUser.Role.TEACHER},
    )
    created_at = models.DateTimeField("Дата привязки", auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("teacher", "student"))]
        verbose_name = "Привязка ученика к учителю"
        verbose_name_plural = "Привязки учеников к учителям"

    def __str__(self):
        return f"{self.student.full_name} → {self.teacher.full_name}"


class Homework(models.Model):
    """Домашнее задание (выдаётся учителем привязанным ученикам)."""

    class AnswerType(models.TextChoices):
        NONE = "NONE", "Без автопроверки (ручная проверка)"
        EXACT = "EXACT", "Точное совпадение"
        NUMERIC = "NUMERIC", "Числовой ответ (с допуском)"
        MULTIPLE = "MULTIPLE", "Несколько вариантов (все верные)"

    title = models.CharField("Название", max_length=200)
    task = models.TextField(
        "Общее условие задания",
        blank=True,
        help_text="Необязательно, если ниже добавлены отдельные задачи. "
                  "Поддерживается LaTeX в формате $...$ (инлайн) и $$...$$ (отдельная строка).",
    )
    task_image = models.ImageField(
        "Изображение задания (формулы)",
        upload_to="homeworks/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])],
        help_text="Можно приложить фото/скриншот задания с формулами.",
    )
    answer_type = models.CharField(
        "Тип ответа",
        max_length=10,
        choices=AnswerType.choices,
        default=AnswerType.NONE,
    )
    expected_answer = models.TextField(
        "Верный ответ (для автопроверки)",
        blank=True,
        help_text="Для «нескольких вариантов» — по одному варианту в строке.",
    )
    numeric_tolerance = models.FloatField(
        "Допуск для числового ответа",
        default=0.0,
        validators=[MinValueValidator(0)],
    )
    require_photo = models.BooleanField(
        "Требовать фото/файл решения",
        default=False,
        help_text="Ученик не сможет отправить ответ без прикреплённого файла.",
    )
    # NOTE: дедлайны и факт выдачи индивидуальны для каждого ученика (HomeworkIssue).
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("created_at",))]
        verbose_name = "Домашнее задание"
        verbose_name_plural = "Домашние задания"

    def __str__(self):
        return self.title

    @property
    def teacher(self):
        """Владелец задания — учитель из строк выдачи."""
        issue = self.issues.select_related("teacher").filter(teacher__isnull=False).first()
        return issue.teacher if issue else None

    def get_absolute_url(self):
        return reverse("homework_detail", args=[self.pk])

    @property
    def is_autograded(self):
        return self.answer_type != self.AnswerType.NONE

    def autograde(self, answer):
        """Автоматическая проверка ответа.

        Возвращает (is_correct, grade, comment).
        """
        from .autograding import grade_answer

        return grade_answer(self, answer)

    @property
    def has_problems(self):
        """Задание состоит из нескольких задач."""
        return self.problems.exists()


class HomeworkIssue(models.Model):
    """Выдача домашнего задания конкретному ученику.

    ДЗ выдаются напрямую по привязке «ученик ↔ учитель» (StudentTeacherLink).
    """

    teacher = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="issued_homeworks",
        verbose_name="Учитель",
        null=True,
        blank=True,
        limit_choices_to={"role": CustomUser.Role.TEACHER},
    )
    student = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="homework_issues",
        verbose_name="Ученик",
        null=True,
        blank=True,
        limit_choices_to={"role": CustomUser.Role.STUDENT},
    )
    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="issues",
        verbose_name="Домашнее задание",
    )
    issued = models.BooleanField("Выдано", default=False)
    issued_at = models.DateTimeField("Выдано", null=True, blank=True)
    deadline = models.DateTimeField("Дедлайн", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("homework", "student"),
                condition=models.Q(student__isnull=False),
                name="unique_issue_per_student",
            ),
        ]
        indexes = [
            models.Index(fields=("student", "issued")),
            models.Index(fields=("teacher", "homework")),
        ]
        verbose_name = "Выдача ДЗ ученику"
        verbose_name_plural = "Выдачи ДЗ ученикам"

    def __str__(self):
        return f"{self.student} — {self.homework}"

    @property
    def is_available(self):
        """Доступно ли задание ученику сейчас."""
        return self.issued


class HomeworkProblem(models.Model):
    """Отдельная задача (пример) в составе домашнего задания."""

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="problems",
        verbose_name="Домашнее задание",
    )
    order = models.PositiveIntegerField("Номер задачи", default=1)
    text = models.TextField(
        "Условие задачи",
        blank=True,
        help_text="Поддерживается LaTeX: $...$ и $$...$$.",
    )
    image = models.ImageField(
        "Изображение задачи",
        upload_to="homeworks/problems/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])],
    )
    answer_type = models.CharField(
        "Тип ответа",
        max_length=10,
        choices=Homework.AnswerType.choices,
        default=Homework.AnswerType.NONE,
    )
    expected_answer = models.TextField(
        "Верный ответ (для автопроверки)",
        blank=True,
        help_text="Для «нескольких вариантов» — по одному варианту в строке.",
    )
    numeric_tolerance = models.FloatField(
        "Допуск для числового ответа",
        default=0.0,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        ordering = ("order", "id")
        unique_together = ("homework", "order")
        indexes = [models.Index(fields=("homework", "order"))]
        verbose_name = "Задача ДЗ"
        verbose_name_plural = "Задачи ДЗ"

    def __str__(self):
        return f"{self.homework.title} — задача {self.order}"

    @property
    def is_autograded(self):
        return self.answer_type != Homework.AnswerType.NONE and bool((self.expected_answer or "").strip())


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
        # FIX: исключаем исполняемые и серверные файлы на уровне модели.
        validators=[
            FileExtensionValidator(
                allowed_extensions=["pdf", "doc", "docx", "odt", "txt", "rtf", "jpg", "jpeg", "png", "webp", "zip"]
            )
        ],
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
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    teacher_comment = models.TextField("Комментарий преподавателя", blank=True)
    submitted_at = models.DateTimeField("Отправлено", auto_now_add=True)
    checked_at = models.DateTimeField("Проверено", null=True, blank=True)

    class Meta:
        unique_together = ("homework", "student")
        ordering = ("-submitted_at",)
        indexes = [
            models.Index(fields=("student", "status")),
            models.Index(fields=("homework", "status")),
        ]
        verbose_name = "Ответ на ДЗ"
        verbose_name_plural = "Ответы на ДЗ"

    def __str__(self):
        return f"{self.student} - {self.homework}"

    @property
    def photo_required(self):
        return self.homework.require_photo


class SubmissionAnswer(models.Model):
    """Ответ ученика на конкретную задачу домашнего задания."""

    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name="Ответ на ДЗ",
    )
    problem = models.ForeignKey(
        HomeworkProblem,
        on_delete=models.CASCADE,
        related_name="submission_answers",
        verbose_name="Задача",
    )
    answer = models.TextField("Ответ", blank=True)

    class Meta:
        unique_together = ("submission", "problem")
        ordering = ("problem__order",)
        verbose_name = "Ответ на задачу"
        verbose_name_plural = "Ответы на задачи"

    def __str__(self):
        return f"{self.submission.student} — задача {self.problem.order}"


class Theme(models.Model):
    """Кастомная тема оформления (настраивается суперпользователем)."""

    name = models.CharField("Название", max_length=100)
    is_active = models.BooleanField("Активна", default=False)
    hex_color_validator = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Укажите HEX-цвет в формате #RRGGBB.")
    css_value_validator = RegexValidator(
        r"^(0|0?\.[0-9]+|[1-9][0-9]*\.?[0-9]*)(px|rem|em|%)$",
        "Используйте значение с единицей px, rem, em или %.",
    )
    font_family_validator = RegexValidator(
        r"^[A-Za-z0-9 ,\'\-]+$",
        "Недопустимое значение шрифта.",
    )
    primary_color = models.CharField("Основной цвет", max_length=7, default="#4F46E5", validators=[hex_color_validator])
    secondary_color = models.CharField("Дополнительный цвет", max_length=7, default="#10B981", validators=[hex_color_validator])
    background_color = models.CharField("Цвет фона", max_length=7, default="#FFFFFF", validators=[hex_color_validator])
    text_color = models.CharField("Цвет текста", max_length=7, default="#111827", validators=[hex_color_validator])
    card_background = models.CharField("Фон карточек", max_length=7, default="#FFFFFF", validators=[hex_color_validator])
    navbar_background = models.CharField("Фон навбара", max_length=7, default="#FFFFFF", validators=[hex_color_validator])
    font_family = models.CharField(
        "Шрифт",
        max_length=100,
        default="Inter, sans-serif",
        validators=[font_family_validator],
    )
    border_radius = models.CharField("Скругление", max_length=20, default="0.5rem", validators=[css_value_validator])
    created_at = models.DateTimeField("Создана", auto_now_add=True)

    class Meta:
        verbose_name = "Тема"
        verbose_name_plural = "themes"

    def __str__(self):
        return f"{self.name}{' (активна)' if self.is_active else ''}"