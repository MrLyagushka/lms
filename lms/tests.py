from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import SubmissionForm
from .models import Course, CustomUser, Enrollment, Homework, Submission, Theme


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
