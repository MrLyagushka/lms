from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

from .models import (
    CustomUser,
    Homework,
    HomeworkIssue,
    HomeworkProblem,
    StudentTeacherLink,
    Submission,
    Theme,
)

User = get_user_model()


class UserRegistrationForm(forms.ModelForm):
    """Регистрация нового пользователя по email и паролю."""

    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={"class": "form-input", "autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        label="Повторите пароль",
        widget=forms.PasswordInput(attrs={"class": "form-input", "autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
        labels = {
            "first_name": "Имя",
            "last_name": "Фамилия",
            "email": "Email",
        }
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Иван"}),
            "last_name": forms.TextInput(attrs={"class": "form-input", "placeholder": "Иванов"}),
            "email": forms.EmailInput(
                attrs={"class": "form-input", "placeholder": "you@example.com"}
            ),
        }

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже зарегистрирован.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Пароли не совпадают.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        # Жёстко фиксируем роль: самостоятельная регистрация — только ученик.
        # Преподаватели создаются администратором в Django-админке.
        user.role = CustomUser.Role.STUDENT
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user



class LinkStudentForm(forms.Form):
    """Учитель привязывает ученика к себе по email (основная связь текущей версии)."""

    email = forms.EmailField(
        label="Email ученика",
        widget=forms.EmailInput(attrs={"class": "form-input", "placeholder": "student@mail.ru"}),
    )

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher = teacher

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        student = User.objects.filter(email__iexact=email, role=User.Role.STUDENT).first()
        if not student:
            raise forms.ValidationError(
                "Ученик с таким email не найден. Пусть зарегистрируется на сайте."
            )
        existing = StudentTeacherLink.objects.filter(student=student).first()
        if existing:
            if self.teacher and existing.teacher_id == self.teacher.pk:
                raise forms.ValidationError("Этот ученик уже привязан к вам.")
            raise forms.ValidationError("Этот ученик уже привязан к другому учителю.")
        self.student = student
        return email


class HomeworkForm(forms.ModelForm):
    """Форма создания / редактирования домашнего задания.

    Здесь только настройки самого задания: ДЗ создаётся учителем
    и выдаётся привязанным ученикам (см. HomeworkIssueStudentsForm).
    """

    class Meta:
        model = Homework
        fields = (
            "title",
            "task",
            "task_image",
            "answer_type",
            "expected_answer",
            "numeric_tolerance",
            "require_photo",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-input"}),
            "task": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 6,
                    "placeholder": "Текст задания. Поддерживается LaTeX: $E=mc^2$ и $$\\int_0^1 x\\,dx$$",
                }
            ),
            "task_image": forms.ClearableFileInput(attrs={"class": "form-file"}),
            "answer_type": forms.Select(attrs={"class": "form-input"}),
            "expected_answer": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 3,
                    "placeholder": "Верный ответ. Для «нескольких вариантов» — по одному в строке.",
                }
            ),
            "numeric_tolerance": forms.NumberInput(
                attrs={"class": "form-input", "min": 0, "step": "0.01", "placeholder": "0.01 = 1%"}
            ),
            "require_photo": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FIX: если ДЗ состоит из отдельных задач, общее условие необязательно.
        self.fields["task"].required = False
        self.fields["numeric_tolerance"].required = False

    def clean(self):
        cleaned = super().clean()
        answer_type = cleaned.get("answer_type")
        expected = (cleaned.get("expected_answer") or "").strip()
        if answer_type in (
            Homework.AnswerType.EXACT,
            Homework.AnswerType.NUMERIC,
            Homework.AnswerType.MULTIPLE,
        ) and not expected:
            self.add_error("expected_answer", "Укажите верный ответ для автопроверки.")
        return cleaned


class HomeworkIssueForm(forms.ModelForm):
    """Настройки выдачи ДЗ конкретному ученику (панель ученика)."""

    class Meta:
        model = HomeworkIssue
        fields = ("issued", "deadline")
        widgets = {
            "issued": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300"}),
            "deadline": forms.DateTimeInput(
                attrs={"class": "form-input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Обязательно, чтобы datetime-local корректно отображал значение
        self.fields["deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["deadline"].required = False
        self.fields["issued"].required = False


class HomeworkIssueStudentsForm(forms.Form):
    """Выдача домашнего задания сразу нескольким ученикам (1–500 задач в самом ДЗ)."""

    students = forms.ModelMultipleChoiceField(
        label="Ученики",
        queryset=None,  # заполняется в __init__
        required=True,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "h-4 w-4 rounded border-slate-300"}),
        help_text="Выберите учеников, которым выдаётся задание.",
    )
    deadline = forms.DateTimeField(
        label="Дедлайн (необязательно)",
        required=False,
        widget=forms.DateTimeInput(
            attrs={"class": "form-input", "type": "datetime-local"},
        ),
        input_formats=["%Y-%m-%dT%H:%M"],
    )

    def __init__(self, *args, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher = teacher
        if teacher is not None:
            self.fields["students"].queryset = User.objects.filter(
                role=User.Role.STUDENT,
                teacher_link__teacher=teacher,
            ).order_by("first_name", "last_name", "email")


class HomeworkProblemForm(forms.ModelForm):
    """Форма одной задачи в составе домашнего задания."""

    class Meta:
        model = HomeworkProblem
        fields = ("order", "text", "image", "answer_type", "expected_answer", "numeric_tolerance")
        widgets = {
            "order": forms.NumberInput(attrs={"class": "form-input", "min": 1}),
            "text": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 3,
                    "placeholder": "Условие задачи. Поддерживается LaTeX: $2x+3=7$",
                }
            ),
            "image": forms.ClearableFileInput(attrs={"class": "form-file"}),
            "answer_type": forms.Select(attrs={"class": "form-input"}),
            "expected_answer": forms.Textarea(
                attrs={
                    "class": "form-input",
                    "rows": 2,
                    "placeholder": "Верный ответ (для автопроверки)",
                }
            ),
            "numeric_tolerance": forms.NumberInput(
                attrs={"class": "form-input", "min": 0, "step": "0.01", "placeholder": "0.01 = 1%"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["text"].required = False
        self.fields["numeric_tolerance"].required = False


HomeworkProblemFormSet = inlineformset_factory(
    Homework,
    HomeworkProblem,
    form=HomeworkProblemForm,
    fields=("order", "text", "image", "answer_type", "expected_answer", "numeric_tolerance"),
    extra=0,
    can_delete=True,
)


class SubmissionForm(forms.ModelForm):
    """Форма ответа ученика на домашнее задание."""

    class Meta:
        model = Submission
        fields = ("answer", "file")
        widgets = {
            "answer": forms.Textarea(
                attrs={"class": "form-input", "rows": 8, "placeholder": "Введите ваш ответ"}
            ),
            "file": forms.ClearableFileInput(attrs={"class": "form-file"}),
        }
        labels = {
            "answer": "Ответ",
            "file": "Прикрепить файл (необязательно)",
        }

    def __init__(self, *args, homework=None, submission=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.homework = homework
        self.problem_fields = []
        if homework is not None and homework.require_photo:
            self.fields["file"].required = True
            self.fields["file"].label = "Фото/файл решения (обязательно)"

        # FIX: ДЗ из нескольких задач — отдельное поле ответа для каждой задачи.
        if homework is not None:
            problems = list(homework.problems.all())
            if problems:
                del self.fields["answer"]
                existing = {}
                if submission is not None:
                    existing = {a.problem_id: a.answer for a in submission.answers.all()}
                for problem in problems:
                    name = f"answer_{problem.pk}"
                    self.problem_fields.append(name)
                    self.fields[name] = forms.CharField(
                        label=f"Задача {problem.order}. {problem.text[:60]}",
                        required=False,
                        widget=forms.Textarea(attrs={"class": "form-input", "rows": 2}),
                        initial=existing.get(problem.pk, ""),
                    )

    def clean(self):
        cleaned = super().clean()
        if self.problem_fields:
            # FIX: для многозадачного ДЗ — требуется хотя бы один ответ или файл.
            if not any((cleaned.get(name) or "").strip() for name in self.problem_fields) and not cleaned.get("file"):
                raise forms.ValidationError("Ответьте хотя бы на одну задачу или прикрепите файл решения.")
        else:
            # FIX: не принимаем пустую отправку без текста и файла.
            if not cleaned.get("answer", "").strip() and not cleaned.get("file"):
                raise forms.ValidationError("Добавьте текст ответа или прикрепите файл.")
        # FIX: если задание требует фото решения — файл обязателен.
        if self.homework is not None and self.homework.require_photo and not cleaned.get("file"):
            self.add_error("file", "Для этого задания обязательно приложите фото или файл решения.")
        return cleaned


class GradeForm(forms.ModelForm):
    """Форма проверки ответа преподавателем."""

    class Meta:
        model = Submission
        fields = ("status", "grade", "teacher_comment")
        widgets = {
            "status": forms.Select(attrs={"class": "form-input"}),
            "grade": forms.NumberInput(attrs={"class": "form-input", "min": 0, "max": 100}),
            "teacher_comment": forms.Textarea(
                attrs={"class": "form-input", "rows": 5, "placeholder": "Комментарий для ученика"}
            ),
        }


class ProfileForm(forms.ModelForm):
    """Форма редактирования профиля."""

    class Meta:
        model = CustomUser
        fields = ("first_name", "last_name", "email", "bio", "avatar")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-input"}),
            "last_name": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "bio": forms.Textarea(attrs={"class": "form-input", "rows": 4}),
            "avatar": forms.ClearableFileInput(attrs={"class": "form-file"}),
        }

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        qs = CustomUser.objects.filter(email__iexact=email)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Этот email уже занят другим пользователем.")
        return email


class StudentManageForm(forms.ModelForm):
    """Преподаватель редактирует параметры ученика (панель «Ученики»)."""

    new_password = forms.CharField(
        label="Новый пароль (необязательно)",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-input", "placeholder": "Оставьте пустым, чтобы не менять", "autocomplete": "new-password"}
        ),
        help_text="Если заполнить — пароль ученика будет сброшен на новый.",
    )

    class Meta:
        model = CustomUser
        fields = ("first_name", "last_name", "email", "bio", "avatar", "is_active")
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-input"}),
            "last_name": forms.TextInput(attrs={"class": "form-input"}),
            "email": forms.EmailInput(attrs={"class": "form-input"}),
            "bio": forms.Textarea(attrs={"class": "form-input", "rows": 3}),
            "avatar": forms.ClearableFileInput(attrs={"class": "form-file"}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = False
        self.fields["last_name"].required = False
        self.fields["is_active"].label = "Аккаунт активен (ученик может входить в систему)"

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        qs = CustomUser.objects.filter(email__iexact=email)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Этот email уже занят другим пользователем.")
        return email


FONT_CHOICES = [
    ("Inter, sans-serif", "Inter"),
    ("Roboto, sans-serif", "Roboto"),
    ("'Segoe UI', sans-serif", "Segoe UI"),
    ("Georgia, serif", "Georgia"),
    ("'Courier New', monospace", "Courier New"),
    ("system-ui, sans-serif", "System UI"),
]


class ThemeForm(forms.ModelForm):
    """Форма создания / редактирования кастомной темы."""

    # FIX: раньше font_family был ChoiceField с фиксированным списком —
    # тему со шрифтом вне списка (например, заданным через админку) было
    # невозможно сохранить: валидация выдавала «Выбран неверный вариант»,
    # а ошибка нигде не показывалась. Теперь поле генерируется из модели
    # (CharField + font_family_validator), а FONT_CHOICES используются
    # как подсказки (datalist) в шаблоне.

    class Meta:
        model = Theme
        fields = (
            "name",
            "primary_color",
            "secondary_color",
            "background_color",
            "text_color",
            "card_background",
            "navbar_background",
            "font_family",
            "border_radius",
            "is_active",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-input"}),
            "primary_color": forms.TextInput(attrs={"class": "form-color", "type": "color"}),
            "secondary_color": forms.TextInput(attrs={"class": "form-color", "type": "color"}),
            "background_color": forms.TextInput(attrs={"class": "form-color", "type": "color"}),
            "text_color": forms.TextInput(attrs={"class": "form-color", "type": "color"}),
            "card_background": forms.TextInput(attrs={"class": "form-color", "type": "color"}),
            "navbar_background": forms.TextInput(attrs={"class": "form-color", "type": "color"}),
            "font_family": forms.TextInput(
                attrs={"class": "form-input", "list": "font-options", "placeholder": "Inter, sans-serif"}
            ),
            "border_radius": forms.TextInput(attrs={"class": "form-input", "placeholder": "0.5rem"}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300"}),
        }