"""Автоматическая проверка ответов на домашние задания.

Поддерживаемые типы (Homework.AnswerType):
- EXACT:    точное совпадение (нормализация пробелов/регистрастра/знаков)
- NUMERIC:  числовой ответ с относительным допуском
- MULTIPLE: несколько верных вариантов (все должны быть указаны учеником)
"""

import re

from .models import Homework

# Символы, которые нормализуем при сравнении: пробелы, переносы,
# запятая как десятичный разделитель, лишние скобки/знаки.
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(value):
    """Нормализация текстового ответа для сравнения."""
    if value is None:
        return ""
    text = str(value)
    # Запятая -> точка (русский формат чисел)
    text = text.replace(",", ".")
    # Убираем пробелы
    text = _WHITESPACE_RE.sub("", text)
    # Убираем лишние скобки и пробелы вокруг операторов
    text = text.replace("(", "").replace(")", "")
    return text.strip().lower()


def _to_float(value):
    """Попытка привести строку к float. Возвращает None при неудаче."""
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    # Убираем пробелы и лишние символы
    text = _WHITESPACE_RE.sub("", text)
    # Убираем % в конце
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _grade_by_type(answer_type, expected, answer, tolerance=0.0):
    """Проверка одного ответа по типу. Возвращает (is_correct, grade, comment)."""
    expected = (expected or "").strip()
    if not expected:
        return None, None, "Верный ответ не задан — требуется ручная проверка."

    if answer_type == Homework.AnswerType.EXACT:
        return _grade_exact(expected, answer)
    if answer_type == Homework.AnswerType.NUMERIC:
        return _grade_numeric(expected, answer, tolerance)
    if answer_type == Homework.AnswerType.MULTIPLE:
        return _grade_multiple(expected, answer)

    return None, None, "Неизвестный тип автопроверки."


def grade_answer(homework, answer):
    """Проверяет ответ ученика на задание с одним условием.

    Возвращает кортеж (is_correct, grade, comment).
    grade: int 0..100 или None (если автопроверка не настроена).
    """
    if not homework.is_autograded:
        return None, None, "Автопроверка не настроена — требуется ручная проверка."

    return _grade_by_type(
        homework.answer_type,
        homework.expected_answer,
        answer,
        homework.numeric_tolerance,
    )


def grade_problem(problem, answer):
    """Проверяет ответ на одну задачу домашнего задания."""
    if not problem.is_autograded:
        return None, None, "Задача без автопроверки."
    return _grade_by_type(
        problem.answer_type,
        problem.expected_answer,
        answer,
        problem.numeric_tolerance,
    )


def grade_multi(homework, problem_answers):
    """Проверяет ответы на несколько задач.

    problem_answers: dict {problem_id: ответ}.
    Возвращает (all_correct, grade, comment); grade — процент верных задач.
    """
    autograded = [p for p in homework.problems.all() if p.is_autograded]
    if not autograded:
        return None, None, "Автопроверка не настроена — требуется ручная проверка."

    correct = 0
    wrong = []
    for problem in autograded:
        is_correct, _grade, _comment = grade_problem(problem, problem_answers.get(problem.pk, ""))
        if is_correct:
            correct += 1
        else:
            wrong.append(str(problem.order))

    total = len(autograded)
    score = round(100 * correct / total)
    if correct == total:
        return True, 100, f"Все {total} задач решены верно."
    return False, score, f"Верно {correct} из {total}. Ошибки в задачах: {', '.join(wrong)}."


def _grade_exact(expected, answer):
    exp = _normalize_text(expected)
    got = _normalize_text(answer)
    if exp == got:
        return True, 100, "Верный ответ."
    return False, 0, f"Неверный ответ. Ожидалось: {expected.strip()}"


def _grade_numeric(expected, answer, tolerance):
    exp_val = _to_float(expected)
    got_val = _to_float(answer)
    if exp_val is None or got_val is None:
        return False, 0, "Ответ должен быть числом."
    if tolerance is None or tolerance < 0:
        tolerance = 0.0
    # Относительный допуск: 0.01 = 1%
    if exp_val == 0:
        ok = abs(got_val) <= tolerance
    else:
        ok = abs(got_val - exp_val) / abs(exp_val) <= tolerance
    if ok:
        return True, 100, "Верный ответ."
    return False, 0, f"Неверный ответ. Ожидалось: {expected.strip()}"


def _grade_multiple(expected, answer):
    # Разделяем варианты по строкам
    expected_set = {
        _normalize_text(line)
        for line in expected.splitlines()
        if line.strip()
    }
    # Ответ ученика: разделяем по запятым/строкам/точкам с запятой
    got_raw = str(answer or "")
    got_parts = re.split(r"[,;\n]", got_raw)
    got_set = {_normalize_text(p) for p in got_parts if p.strip()}

    if not expected_set:
        return None, None, "Верные варианты не заданы."

    if got_set == expected_set:
        return True, 100, "Верный ответ."
    missing = expected_set - got_set
    extra = got_set - expected_set
    parts = []
    if missing:
        parts.append(f"не хватает: {', '.join(sorted(missing))}")
    if extra:
        parts.append(f"лишние: {', '.join(sorted(extra))}")
    detail = "; ".join(parts) if parts else "неверный ответ"
    return False, 0, f"Неверный ответ ({detail})."
