/*
 * Панель вставки LaTeX-формул.
 *
 * Работает через делегирование событий: кнопки с классом .latex-btn
 * внутри контейнера [data-latex-toolbar] вставляют свой data-ins в
 * ближайший textarea того же блока. Подходит и для динамически
 * добавленных строк formset (шаблон <template> копирует кнопки вместе
 * с рядом).
 *
 * Соглашение по data-ins: пара {} — место постановки курсора.
 * Если в textarea есть выделение, оно подставляется вместо {}.
 */
(function () {
    "use strict";

    document.addEventListener("click", function (e) {
        var btn = e.target.closest(".latex-btn");
        if (!btn) { return; }
        e.preventDefault();

        var toolbar = btn.closest("[data-latex-toolbar]");
        var host = toolbar ? toolbar.parentElement : null;
        var ta = host ? host.querySelector("textarea") : null;
        if (!ta) { return; }

        var snip = btn.getAttribute("data-ins") || "";
        var start = ta.selectionStart;
        var end = ta.selectionEnd;
        var sel = ta.value.slice(start, end);

        var ins, caret;
        var brace = snip.indexOf("{}");
        if (brace !== -1) {
            if (sel) {
                // Оборачиваем выделенный текст: $2x+1$ -> $\sqrt{2x+1}$
                ins = snip.slice(0, brace) + sel + snip.slice(brace + 2);
                caret = start + ins.length;
            } else {
                ins = snip;
                caret = start + brace + 1; // курсор между { }
            }
        } else {
            ins = snip;
            caret = start + ins.length;
        }

        ta.focus();
        // setRangeText сохраняет нативную историю отмены (Ctrl+Z)
        ta.setRangeText(ins, start, end, "end");
        ta.setSelectionRange(caret, caret);
        ta.dispatchEvent(new Event("input", { bubbles: true }));
    });
})();
