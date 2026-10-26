"""Тег шаблона для рендеринга LaTeX-формул через KaTeX.

Использование в шаблоне:
    {% load lms_extras %}
    {{ text|latex }}

Поддерживаются:
- $...$   — инлайн-формула
- $$...$$ — формула на отдельной строке (display)
"""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

# $$...$$ (display) и $...$ (inline). Сначала display, чтобы не пересечься.
_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_RE = re.compile(r"\$(.+?)\$")


def _render_latex(text):
    """Конвертирует LaTeX-разметку в HTML с KaTeX-классами.

    Формулы оборачиваются в <span class="katex-src">...</span> —
    JS в base.html затем вызывает katex.render на таких элементах.
    """
    if not text:
        return ""

    # Экранируем HTML, чтобы пользовательский текст не вставлял разметку.
    safe = escape(text)

    def display_sub(match):
        formula = match.group(1)
        return f'<span class="katex-display katex-src" data-formula="{formula}"></span>'

    def inline_sub(match):
        formula = match.group(1)
        return f'<span class="katex-src" data-formula="{formula}"></span>'

    safe = _DISPLAY_RE.sub(display_sub, safe)
    safe = _INLINE_RE.sub(inline_sub, safe)
    # mark_safe нужен, иначе Django автоэкранированием дважды экранирует
    # HTML и вместо отрисовки KaTeX пользователь увидит текст <span ...>.
    # Само содержимое уже пропущено через escape() выше, поэтому безопасно.
    return mark_safe(safe)


@register.filter(name="latex")
def latex(value):
    """Формат: {{ value|latex }} — рендерит $...$ и $$...$$ через KaTeX."""
    return _render_latex(value)
