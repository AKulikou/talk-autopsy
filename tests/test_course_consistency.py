"""C2b measurement (talk_autopsy/course_consistency.py) on invented lessons."""

from talk_autopsy.course_consistency import OTHER, Lesson, measure, rendering, unit_label


def lesson(name, units, glossary):
    return Lesson(name, units, {k.casefold(): set(v) for k, v in glossary.items()},
                  {k.casefold(): k for k in glossary})


def test_a_term_rendered_two_ways_is_inconsistent_and_one_way_is_not():
    g = {"storage slot": ["слот хранилища", "ячейка памяти"], "Foundry": []}
    one = lesson("1", [("A storage slot here.", "Здесь слот хранилища."),
                       ("Use Foundry.", "Используйте Foundry.")], g)
    two = lesson("2", [("Each storage slot.", "Каждая ячейка памяти."),
                       ("Foundry again.", "Снова Foundry.")], g)
    three = lesson("3", [("No terms here.", "Здесь нет терминов.")], g)
    result = measure([one, two, three], [one, two, three])
    assert result.multi == 2 and [x["term"] for x in result.inconsistent] == ["storage slot"]
    same = lesson("2b", [("Each storage slot.", "Каждый слот хранилища.")], g)
    assert measure([one, same], [one, two, same]).inconsistent == []


def test_labels_inflections_latin_and_other():
    candidates = {"цена газа"}
    assert unit_label("gas price", "Смотрим на цену газа.", candidates) == "цена газа"
    assert unit_label("gas price", "Смотрим на gas price.", candidates) == "keep:gas price"
    assert unit_label("gas price", "Смотрим на стоимость.", candidates) == OTHER
    both = unit_label("gas price", "Цена газа (gas price).", candidates)
    assert both == "keep:gas price + цена газа"


def test_the_rendering_of_a_lesson_is_the_majority_label():
    g = {"event": ["событие"]}
    x = lesson("1", [("An event.", "Событие."), ("The event.", "Событие."), ("One event.", "Один ивент.")], g)
    assert rendering("event", x, {"событие"}) == "событие"
    assert rendering("mapping", x, set()) is None
