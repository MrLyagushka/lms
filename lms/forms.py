from django import forms
from django.contrib.auth import get_user_model

from .models import Course, CustomUser, Homework, Lesson, Submission, Theme

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



class CourseForm(forms.ModelForm):
    """Форма создания / редактирования курса."""

    class Meta:
        model = Course
        fields = ("title", "subject", "description", "cover")
        widgets = {
            "title": forms.TextInput(
                attrs={"class": "form-input", "placeholder": "Например: Подготовка к ЕГЭ по математике"}
            ),
            "subject": forms.Select(attrs={"class": "form-input"}),
            "description": forms.Textarea(
                attrs={"class": "form-input", "rows": 4, "placeholder": "Кратко опишите курс"}
            ),
        }


class LessonForm(forms.ModelForm):
    """Форма создания / редактирования урока."""

    class Meta:
        model = Lesson
        fields = ("title", "order", "content")
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-input"}),
            "order": forms.NumberInput(attrs={"class": "form-input", "min": 1}),
            "content": forms.Textarea(attrs={"class": "form-input", "rows": 8}),
        }


class HomeworkForm(forms.ModelForm):
    """Форма создания / редактирования домашнего задания."""

    class Meta:
        model = Homework
        fields = ("title", "lesson", "task", "deadline")
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-input"}),
            "lesson": forms.Select(attrs={"class": "form-input"}),
            "task": forms.Textarea(attrs={"class": "form-input", "rows": 6}),
            "deadline": forms.DateTimeInput(
                attrs={"class": "form-input", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        if course is not None:
            self.fields["lesson"].queryset = course.lessons.all()
        # Обязательно, чтобы datetime-local корректно отображал значение
        self.fields["deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["deadline"].required = False


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

    font_family = forms.ChoiceField(
        label="Шрифт",
        choices=FONT_CHOICES,
        widget=forms.Select(attrs={"class": "form-input"}),
    )

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
            "border_radius": forms.TextInput(attrs={"class": "form-input", "placeholder": "0.5rem"}),
            "is_active": forms.CheckboxInput(attrs={"class": "h-4 w-4 rounded border-slate-300"}),
        }