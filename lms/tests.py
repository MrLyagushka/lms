from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.utils.html import escape
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import SubmissionForm
from .models import (
    Course,
    CustomUser,
    Enrollment,
    GradeLevel,
    Homework,
    HomeworkIssue,
    HomeworkProblem,
    Lesson,
    LessonDate,
    Submission,
    Theme,
)


@override_settings(SECURE_SSL_REDIRECT=False)
class SecurityRegressionTests(TestCase):
	def setUp(self):
		self.teacher = CustomUser.objects.create_user(
			email="teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.student = CustomUser.objects.create_user(
			email="student@example.com", password="Strong-pass-123"
		)
		self.other_student = CustomUser.objects.create_user(
			email="other@example.com", password="Strong-pass-123"
		)
		self.course = Course.objects.create(
			title="Опубликованный курс", teacher=self.teacher, is_published=True
		)
		self.private_course = Course.objects.create(
			title="Черновик курса", teacher=self.teacher, is_published=False
		)
		self.homework = Homework.objects.create(course=self.course, title="ДЗ", task="Решите задачу")
		Enrollment.objects.create(student=self.student, course=self.course)
		self.submission = Submission.objects.create(
			homework=self.homework, student=self.student, answer="Ответ"
		)

	def test_private_course_is_not_visible_to_other_student(self):
		self.client.force_login(self.other_student)
		response = self.client.get(reverse("course_detail", args=[self.private_course.pk]))
		self.assertRedirects(response, reverse("course_list"))

	def test_submission_file_is_not_downloadable_by_other_student(self):
		self.submission.file.save(
			"answer.txt", SimpleUploadedFile("answer.txt", b"private answer"), save=True
		)
		self.client.force_login(self.other_student)
		response = self.client.get(reverse("secure_media", args=[self.submission.file.name]))
		self.assertEqual(response.status_code, 404)

	def test_submission_form_rejects_executable_extension_and_empty_answer(self):
		empty_form = SubmissionForm(data={"answer": "", "file": ""})
		self.assertFalse(empty_form.is_valid())
		executable_form = SubmissionForm(
			data={"answer": "Ответ"},
			files={"file": SimpleUploadedFile("payload.exe", b"not safe")},
		)
		self.assertFalse(executable_form.is_valid())

	def test_theme_rejects_invalid_css_values(self):
		theme = Theme(
			name="Bad theme",
			primary_color="#ZZZ",
			font_family="Arial; color: red",
		)
		with self.assertRaises(ValidationError):
			theme.full_clean()


@override_settings(SECURE_SSL_REDIRECT=False)
class AutogradingTests(TestCase):
	"""Тесты автоматической проверки ответов."""

	def setUp(self):
		self.teacher = CustomUser.objects.create_user(
			email="teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.student = CustomUser.objects.create_user(
			email="student@example.com", password="Strong-pass-123"
		)
		self.course = Course.objects.create(
			title="Курс", teacher=self.teacher, is_published=True
		)
		Enrollment.objects.create(student=self.student, course=self.course)

	def test_exact_answer_correct_and_wrong(self):
		hw = Homework.objects.create(
			course=self.course,
			title="ДЗ",
			task="Решите",
			answer_type=Homework.AnswerType.EXACT,
			expected_answer="42",
		)
		self.assertTrue(hw.is_autograded)
		ok, grade, _ = hw.autograde(" 42 ")
		self.assertTrue(ok)
		self.assertEqual(grade, 100)
		ok, grade, _ = hw.autograde("43")
		self.assertFalse(ok)
		self.assertEqual(grade, 0)

	def test_numeric_answer_with_tolerance(self):
		hw = Homework.objects.create(
			course=self.course,
			title="ДЗ",
			task="Решите",
			answer_type=Homework.AnswerType.NUMERIC,
			expected_answer="100",
			numeric_tolerance=0.01,
		)
		ok, grade, _ = hw.autograde("100.5")
		self.assertTrue(ok)
		self.assertEqual(grade, 100)
		ok, grade, _ = hw.autograde("105")
		self.assertFalse(ok)
		self.assertEqual(grade, 0)
		# Запятая как десятичный разделитель
		ok, grade, _ = hw.autograde("100,0")
		self.assertTrue(ok)

	def test_multiple_answer(self):
		hw = Homework.objects.create(
			course=self.course,
			title="ДЗ",
			task="Выберите варианты",
			answer_type=Homework.AnswerType.MULTIPLE,
			expected_answer="a\nb",
		)
		ok, grade, _ = hw.autograde("a, b")
		self.assertTrue(ok)
		self.assertEqual(grade, 100)
		ok, grade, _ = hw.autograde("a")
		self.assertFalse(ok)
		self.assertEqual(grade, 0)

	def test_no_autograding_configured(self):
		hw = Homework.objects.create(course=self.course, title="ДЗ", task="Решите")
		self.assertFalse(hw.is_autograded)
		ok, grade, comment = hw.autograde("42")
		self.assertIsNone(ok)
		self.assertIsNone(grade)

	def test_submission_autogrades_on_create(self):
		hw = Homework.objects.create(
			course=self.course,
			title="ДЗ",
			task="Решите",
			answer_type=Homework.AnswerType.EXACT,
			expected_answer="42",
		)
		self.client.force_login(self.student)
		response = self.client.post(
			reverse("submission_create", args=[hw.pk]),
			{"answer": "42"},
		)
		self.assertRedirects(response, hw.get_absolute_url())
		submission = Submission.objects.get(homework=hw, student=self.student)
		self.assertEqual(submission.status, Submission.Status.CHECKED)
		self.assertEqual(submission.grade, 100)
		self.assertIsNotNone(submission.checked_at)

	def test_submission_wrong_answer_autogrades_zero(self):
		hw = Homework.objects.create(
			course=self.course,
			title="ДЗ",
			task="Решите",
			answer_type=Homework.AnswerType.EXACT,
			expected_answer="42",
		)
		self.client.force_login(self.student)
		self.client.post(reverse("submission_create", args=[hw.pk]), {"answer": "43"})
		submission = Submission.objects.get(homework=hw, student=self.student)
		self.assertEqual(submission.status, Submission.Status.CHECKED)
		self.assertEqual(submission.grade, 0)


@override_settings(SECURE_SSL_REDIRECT=False)
class AutoIssueTests(TestCase):
	"""Тесты автовыдачи ДЗ после отметки о проведённом занятии (панель ученика)."""

	def setUp(self):
		self.teacher = CustomUser.objects.create_user(
			email="teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.student = CustomUser.objects.create_user(
			email="student@example.com", password="Strong-pass-123"
		)
		self.course = Course.objects.create(
			title="Курс", teacher=self.teacher, is_published=True
		)
		self.enrollment = Enrollment.objects.create(student=self.student, course=self.course)
		self.lesson = Lesson.objects.create(
			course=self.course, title="Урок 1", content="Материалы"
		)
		self.hw = Homework.objects.create(
			course=self.course,
			lesson=self.lesson,
			title="ДЗ",
			task="Решите",
		)
		self.issue = HomeworkIssue.objects.create(
			enrollment=self.enrollment, homework=self.hw, auto_issue=True
		)

	def _conduct(self, conducted=True):
		"""POST панели расписания: дата занятия + отметка «проведено»."""
		prefix = f"l{self.lesson.pk}"
		data = {
			"enrollment": self.enrollment.pk,
			f"{prefix}-date": "2026-10-25T18:00",
		}
		if conducted:
			data[f"{prefix}-is_conducted"] = "on"
		return self.client.post(reverse("student_schedule", args=[self.student.pk]), data)

	def test_unissued_hw_hidden_from_student(self):
		self.client.force_login(self.student)
		response = self.client.get(reverse("homework_detail", args=[self.hw.pk]))
		self.assertRedirects(response, self.course.get_absolute_url())

	def test_conduct_lesson_issues_hw(self):
		self.client.force_login(self.teacher)
		response = self._conduct()
		self.assertRedirects(response, reverse("student_schedule", args=[self.student.pk]))
		lesson_date = LessonDate.objects.get(enrollment=self.enrollment, lesson=self.lesson)
		self.assertTrue(lesson_date.is_conducted)
		self.assertIsNotNone(lesson_date.conducted_at)
		self.issue.refresh_from_db()
		self.assertTrue(self.issue.issued)
		self.assertIsNotNone(self.issue.issued_at)

	def test_conducted_hw_visible_to_student(self):
		self.client.force_login(self.teacher)
		self._conduct()
		self.client.force_login(self.student)
		response = self.client.get(reverse("homework_detail", args=[self.hw.pk]))
		self.assertEqual(response.status_code, 200)

	def test_undo_conduct_unissues_hw(self):
		self.client.force_login(self.teacher)
		self._conduct()
		self._conduct(conducted=False)
		lesson_date = LessonDate.objects.get(enrollment=self.enrollment, lesson=self.lesson)
		self.assertFalse(lesson_date.is_conducted)
		self.issue.refresh_from_db()
		self.assertFalse(self.issue.issued)
		self.assertIsNone(self.issue.issued_at)

	def test_non_owner_cannot_open_schedule(self):
		self.client.force_login(self.student)
		response = self.client.get(reverse("student_schedule", args=[self.student.pk]))
		self.assertRedirects(response, reverse("dashboard"))


@override_settings(SECURE_SSL_REDIRECT=False)
class RequirePhotoTests(TestCase):
	"""Тесты обязательной загрузки фотографии решения."""

	def setUp(self):
		self.teacher = CustomUser.objects.create_user(
			email="teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.student = CustomUser.objects.create_user(
			email="student@example.com", password="Strong-pass-123"
		)
		self.course = Course.objects.create(
			title="Курс", teacher=self.teacher, is_published=True
		)
		Enrollment.objects.create(student=self.student, course=self.course)
		self.hw = Homework.objects.create(
			course=self.course,
			title="ДЗ",
			task="Решите",
			require_photo=True,
		)

	def test_form_requires_photo(self):
		form = SubmissionForm(
			data={"answer": "Решение"},
			homework=self.hw,
		)
		self.assertFalse(form.is_valid())
		self.assertIn("file", form.errors)

	def test_submission_without_photo_rejected(self):
		self.client.force_login(self.student)
		self.client.post(
			reverse("submission_create", args=[self.hw.pk]),
			{"answer": "Решение"},
		)
		self.assertFalse(
			Submission.objects.filter(homework=self.hw, student=self.student).exists()
		)

	def test_submission_with_photo_accepted(self):
		self.client.force_login(self.student)
		response = self.client.post(
			reverse("submission_create", args=[self.hw.pk]),
			{
				"answer": "Решение",
				"file": SimpleUploadedFile(
					"solution.png", b"fake png data", content_type="image/png"
				),
			},
		)
		self.assertRedirects(response, self.hw.get_absolute_url())
		self.assertTrue(
			Submission.objects.filter(homework=self.hw, student=self.student).exists()
		)


@override_settings(SECURE_SSL_REDIRECT=False)
class CourseGradeFilterTests(TestCase):
	"""Тесты фильтрации курсов по классу."""

	def setUp(self):
		self.student = CustomUser.objects.create_user(
			email="student@example.com", password="Strong-pass-123"
		)
		teacher = CustomUser.objects.create_user(
			email="teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		Course.objects.create(
			title="8 класс математика",
			teacher=teacher,
			is_published=True,
			grade_level=GradeLevel.G8,
		)
		Course.objects.create(
			title="9 класс физика",
			teacher=teacher,
			is_published=True,
			grade_level=GradeLevel.G9,
		)

	def test_grade_filter(self):
		self.client.force_login(self.student)
		response = self.client.get(reverse("course_list"), {"grade": "G8"})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["courses"].count(), 1)
		self.assertEqual(response.context["current_grade"], "G8")


@override_settings(SECURE_SSL_REDIRECT=False)
class JsonImportTests(TestCase):
	"""Тесты импорта уроков и ДЗ из JSON (course_import)."""

	def setUp(self):
		self.teacher = CustomUser.objects.create_user(
			email="teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.other_teacher = CustomUser.objects.create_user(
			email="other-teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.course = Course.objects.create(title="Курс для импорта", teacher=self.teacher)
		Lesson.objects.create(course=self.course, title="Существующий урок", order=1)
		self.student = CustomUser.objects.create_user(
			email="pupil@example.com", password="Strong-pass-123"
		)
		Enrollment.objects.create(student=self.student, course=self.course)
		self.payload = {
			"lessons": [
				{
					"title": "Квадратные уравнения",
					"content": "Теорема Виета: $x_1 + x_2 = -b/a$.",
					"homeworks": [
						{
							"title": "ДЗ 1",
							"task": "Решите уравнения.",
							"require_photo": True,
							"deadline": "2025-09-20 18:00",
							"problems": [
								{"text": "x^2-5x+6=0", "answer_type": "MULTIPLE", "expected_answer": "2\n3"},
								{"text": "Найдите произведение корней.", "answer_type": "NUMERIC",
								 "expected_answer": "10", "numeric_tolerance": 0.01},
							],
						}
					],
				}
			],
			"homeworks": [
				{"title": "Доп. задание", "task": "Упростите.", "answer_type": "EXACT", "expected_answer": "a^2"}
			],
		}

	def _post_json(self, payload):
		import json as _json

		return self.client.post(
			reverse("course_import", args=[self.course.pk]),
			{"json_file": SimpleUploadedFile("data.json", _json.dumps(payload, ensure_ascii=False).encode("utf-8"))},
		)

	def test_valid_import_creates_lesson_homework_and_problems(self):
		self.client.force_login(self.teacher)
		response = self._post_json(self.payload)
		self.assertEqual(response.status_code, 302)
		lesson = Lesson.objects.get(title="Квадратные уравнения")
		# Порядок продолжается после существующего урока (order=1).
		self.assertEqual(lesson.order, 2)
		hw = Homework.objects.get(title="ДЗ 1")
		self.assertEqual(hw.lesson, lesson)
		self.assertTrue(hw.require_photo)
		# FIX: дедлайн из JSON применяется к строкам выдачи учеников.
		self.assertTrue(
			HomeworkIssue.objects.filter(homework=hw, deadline__isnull=False).exists()
		)
		self.assertEqual(HomeworkProblem.objects.filter(homework=hw).count(), 2)
		standalone = Homework.objects.get(title="Доп. задание")
		self.assertIsNone(standalone.lesson)
		self.assertEqual(standalone.answer_type, Homework.AnswerType.EXACT)

	def test_import_accepts_plain_list_of_lessons(self):
		self.client.force_login(self.teacher)
		response = self._post_json([{"title": "Урок из списка", "content": "Материалы"}])
		self.assertEqual(response.status_code, 302)
		self.assertTrue(Lesson.objects.filter(title="Урок из списка").exists())

	def test_invalid_payload_rolls_back_everything(self):
		self.client.force_login(self.teacher)
		bad = {
			"lessons": [
				{"title": "Хороший урок", "homeworks": [{"title": "ДЗ", "answer_type": "TELEPATHY"}]},
			]
		}
		response = self._post_json(bad)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context["has_problems"])
		# Ни урок, ни ДЗ не созданы — транзакция откачена.
		self.assertFalse(Lesson.objects.filter(title="Хороший урок").exists())
		self.assertEqual(Homework.objects.count(), 0)

	def test_broken_json_shows_error(self):
		self.client.force_login(self.teacher)
		response = self.client.post(
			reverse("course_import", args=[self.course.pk]),
			{"json_text": "{не является json"},
		)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.context["has_problems"])

	def test_other_teacher_cannot_import(self):
		self.client.force_login(self.other_teacher)
		response = self._post_json(self.payload)
		self.assertEqual(response.status_code, 404)

	def test_student_cannot_open_import_page(self):
		student = CustomUser.objects.create_user(email="student@example.com", password="Strong-pass-123")
		self.client.force_login(student)
		response = self.client.get(reverse("course_import", args=[self.course.pk]))
		self.assertEqual(response.status_code, 302)


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsPageTests(TestCase):
	"""Тесты страницы «Настройки» (темы + JSON-документация)."""

	def setUp(self):
		self.admin = CustomUser.objects.create_superuser(
			email="admin@example.com", password="Strong-pass-123"
		)
		self.teacher = CustomUser.objects.create_user(
			email="teacher@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)

	def test_settings_shows_themes_section(self):
		self.client.force_login(self.admin)
		response = self.client.get(reverse("settings"))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["section"], "themes")
		self.assertTemplateUsed(response, "lms/settings.html")

	def test_json_docs_section(self):
		self.client.force_login(self.admin)
		response = self.client.get(reverse("settings_json"))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["section"], "json")
		self.assertTemplateUsed(response, "lms/settings_json.html")

	def test_old_theme_settings_url_still_works(self):
		self.client.force_login(self.admin)
		response = self.client.get(reverse("theme_settings"))
		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, "lms/settings.html")

	def test_settings_create_theme_via_post(self):
		self.client.force_login(self.admin)
		response = self.client.post(
			reverse("settings"),
			{
				"name": "Тёмная",
				"primary_color": "#111111",
				"secondary_color": "#222222",
				"background_color": "#000000",
				"text_color": "#FFFFFF",
				"card_background": "#111111",
				"navbar_background": "#111111",
				"font_family": "Inter, sans-serif",
				"border_radius": "0.5rem",
				"is_active": "on",
			},
		)
		self.assertRedirects(response, reverse("settings"))
		theme = Theme.objects.get(name="Тёмная")
		self.assertTrue(theme.is_active)

	def test_non_superuser_cannot_open_settings(self):
		self.client.force_login(self.teacher)
		response = self.client.get(reverse("settings"))
		self.assertEqual(response.status_code, 302)


@override_settings(SECURE_SSL_REDIRECT=False)
class LatexFilterTests(TestCase):
	"""Фильтр latex: корни/модули превращаются в span для KaTeX,
	HTML-инъекции остаются экранированными."""

	def _render(self, text):
		return Template("{% load lms_extras %}{{ t|latex }}").render(Context({"t": text}))

	def test_inline_formulas_become_katex_spans(self):
		out = self._render(r"Корень $\sqrt{2}$ и модуль $|x-1|$")
		# Двойного экранирования быть не должно (иначе KaTeX не отрендерит):
		# span приходит в браузер настоящим тегом, а не экранированным текстом.
		self.assertIn('<span class="katex-src"', out)
		self.assertIn(r'data-formula="\sqrt{2}"', out)
		self.assertIn('data-formula="|x-1|"', out)

	def test_display_formula_marked(self):
		out = self._render(r"$$\frac{a^2}{b}$$")
		self.assertIn("katex-display", out)
		self.assertIn(r'data-formula="\frac{a^2}{b}"', out)

	def test_html_injection_still_escaped(self):
		out = self._render("<script>alert(1)</script> $x$")
		self.assertNotIn("<script>", out)
		self.assertIn(escape("<script>"), out)
		# Формула при этом всё равно обрабатывается.
		self.assertIn('data-formula="x"', out)


@override_settings(SECURE_SSL_REDIRECT=False)
class StudentsPanelTests(TestCase):
	"""Панель учителя «Ученики»: просмотр, редактирование, привязка курсов."""

	def setUp(self):
		self.teacher = CustomUser.objects.create_user(
			email="t@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.other_teacher = CustomUser.objects.create_user(
			email="t2@example.com", password="Strong-pass-123", role=CustomUser.Role.TEACHER
		)
		self.student = CustomUser.objects.create_user(
			email="s@example.com",
			password="Strong-pass-123",
			first_name="Иван",
			last_name="Петров",
		)
		self.course = Course.objects.create(title="Мой курс", teacher=self.teacher, is_published=True)
		self.other_course = Course.objects.create(title="Чужой курс", teacher=self.other_teacher, is_published=True)
		self.enrollment = Enrollment.objects.create(student=self.student, course=self.course)

	def test_student_cannot_open_panel(self):
		self.client.force_login(self.student)
		for url in ("students_list",):
			response = self.client.get(reverse(url))
			self.assertEqual(response.status_code, 302)

	def test_teacher_sees_enrolled_student(self):
		self.client.force_login(self.teacher)
		response = self.client.get(reverse("students_list"))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Иван Петров")

	def test_other_teacher_does_not_see_student(self):
		self.client.force_login(self.other_teacher)
		response = self.client.get(reverse("students_list"))
		self.assertNotContains(response, "s@example.com")
		# Прямой доступ к чужому ученику запрещён.
		response = self.client.get(reverse("student_detail", args=[self.student.pk]))
		self.assertEqual(response.status_code, 404)
		response = self.client.get(reverse("student_edit", args=[self.student.pk]))
		self.assertEqual(response.status_code, 404)

	def test_search_by_name(self):
		self.client.force_login(self.teacher)
		response = self.client.get(reverse("students_list"), {"q": "Петров"})
		self.assertContains(response, "Иван Петров")
		response = self.client.get(reverse("students_list"), {"q": "несуществующий"})
		self.assertNotContains(response, "Иван Петров")

	def test_detail_shows_course_and_stats(self):
		self.client.force_login(self.teacher)
		hw = Homework.objects.create(course=self.course, title="ДЗ", task="Решите")
		Submission.objects.create(homework=hw, student=self.student, answer="42", status="NEW")
		response = self.client.get(reverse("student_detail", args=[self.student.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Мой курс")
		self.assertEqual(response.context["submissions_count"], 1)
		self.assertEqual(response.context["new_count"], 1)

	def test_teacher_edits_student_params_and_resets_password(self):
		self.client.force_login(self.teacher)
		response = self.client.post(
			reverse("student_edit", args=[self.student.pk]),
			{
				"first_name": "Пётр",
				"last_name": "Сидоров",
				"email": "petr@example.com",
				"bio": "Готовлюсь к профильному ЕГЭ",
				"is_active": "on",
				"new_password": "New-pass-456",
			},
		)
		self.assertRedirects(response, reverse("student_detail", args=[self.student.pk]))
		self.student.refresh_from_db()
		self.assertEqual(self.student.first_name, "Пётр")
		self.assertEqual(self.student.email, "petr@example.com")
		self.assertEqual(self.student.bio, "Готовлюсь к профильному ЕГЭ")
		self.assertTrue(self.student.is_active)
		self.assertTrue(self.student.check_password("New-pass-456"))

	def test_deactivate_student(self):
		self.client.force_login(self.teacher)
		self.client.post(
			reverse("student_edit", args=[self.student.pk]),
			{"first_name": "Иван", "last_name": "Петров", "email": "s@example.com"},
		)
		self.student.refresh_from_db()
		self.assertFalse(self.student.is_active)

	def test_duplicate_email_rejected(self):
		CustomUser.objects.create_user(email="busy@example.com", password="Strong-pass-123")
		self.client.force_login(self.teacher)
		response = self.client.post(
			reverse("student_edit", args=[self.student.pk]),
			{"first_name": "Иван", "last_name": "Петров", "email": "busy@example.com", "is_active": "on"},
		)
		self.assertEqual(response.status_code, 200)  # форма с ошибкой, без сохранения
		self.student.refresh_from_db()
		self.assertEqual(self.student.email, "s@example.com")

	def test_enroll_and_unenroll_course(self):
		self.client.force_login(self.teacher)
		c2 = Course.objects.create(title="Второй курс", teacher=self.teacher, is_published=True)
		response = self.client.post(
			reverse("student_enroll_course", args=[self.student.pk]), {"course_id": c2.pk}
		)
		self.assertRedirects(response, reverse("student_detail", args=[self.student.pk]))
		self.assertTrue(Enrollment.objects.filter(student=self.student, course=c2).exists())
		# Чужой курс привязать нельзя.
		response = self.client.post(
			reverse("student_enroll_course", args=[self.student.pk]), {"course_id": self.other_course.pk}
		)
		self.assertEqual(response.status_code, 404)
		# Отвязка.
		response = self.client.post(
			reverse("student_unenroll_course", args=[self.student.pk, self.enrollment.pk])
		)
		self.assertRedirects(response, reverse("student_detail", args=[self.student.pk]))
		self.assertFalse(Enrollment.objects.filter(pk=self.enrollment.pk).exists())

	def test_latex_renders_in_homework_detail(self):
		"""Ответ ученика с формулой отдаётся как span для KaTeX (без двойного экранирования)."""
		hw = Homework.objects.create(course=self.course, title="ДЗ", task=r"Найдите $\sqrt{2}$")
		Submission.objects.create(homework=hw, student=self.student, answer=r"$|x-1| = 2$")
		self.client.force_login(self.student)
		response = self.client.get(reverse("homework_detail", args=[hw.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertIn(b'class="katex-src"', response.content)
		self.assertIn(rb'data-formula="|x-1| = 2"', response.content)
		self.assertNotIn(escape("<span").encode(), response.content)
