"""
The course glossary (web/course_glossary.py): which terms and renderings go in,
the cap, the per-lesson snapshot, and the precedence when it is combined with
the domain dictionary into a run's user glossary.
"""

from talk_autopsy import user_glossary, youtube
from talk_autopsy.glossary import Glossary, GlossaryEntry, load_glossary, to_markdown
from talk_autopsy.segments import Segment, Transcript, Word, save_transcript
from talk_autopsy.web import course_glossary as cg


def lesson_work(tmp_path, name, auto: list[GlossaryEntry], units: list[tuple[str, str]]):
    work = tmp_path / "work" / name
    work.mkdir(parents=True)
    (work / "glossary.auto.md").write_text(to_markdown(Glossary(entries=auto)), encoding="utf-8")
    english = [Segment(id=k, start=float(k), end=k + 1.0, text=en) for k, (en, _) in enumerate(units, 1)]
    save_transcript(Transcript(segments=english, language="en", source="fake"), work / "fixed.json")
    ru = [Segment(id=k, start=float(k), end=k + 1.0, text=tr,
                  words=[Word(text=w, start=float(k), end=k + 1.0) for w in en.split()])
          for k, (en, tr) in enumerate(units, 1)]
    save_transcript(Transcript(segments=ru, language="ru", source="fake"), work / "ru.json")
    return work


def auto(term, keep=False, suggested=""):
    notes = "technical term" + (f"; suggested ru: {suggested}" if suggested else "")
    return GlossaryEntry(term=term, keep=keep, notes=notes)


def test_a_used_suggestion_becomes_the_agreed_rendering_and_an_unused_one_stays_a_note(tmp_path):
    work = lesson_work(tmp_path, "yt-a", [auto("storage slot", suggested="слот хранилища"),
                                          auto("gas price", suggested="цена газа"),
                                          auto("Foundry", keep=True)],
                       [("Each storage slot holds 32 bytes.", "Каждый слот хранилища содержит 32 байта."),
                        ("Check the gas price first.", "Сначала проверьте стоимость газа."),
                        ("Install Foundry now.", "Установите Foundry.")])
    course = tmp_path / "course"
    course.mkdir()
    cg.after_lesson(course, "01 Intro", 1, work, "ru")
    glossary = load_glossary(cg.course_file(course))
    slot, gas, foundry = (glossary.find(t) for t in ("storage slot", "gas price", "Foundry"))
    assert slot.translations == {"ru": "слот хранилища"} and "suggested ru" not in slot.notes
    assert gas.translations == {} and "suggested ru: цена газа" in gas.notes  # the lesson said стоимость
    assert foundry.keep and "course: lesson 1" in foundry.notes
    assert cg.summary(course) == {"terms": 3, "lessons": 1, "file": str(cg.course_file(course))}


def test_merged_once_first_wins_counts_add_up_cap_keeps_the_most_frequent(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "MAX_TERMS", 2)
    course = tmp_path / "course"
    course.mkdir()
    one = lesson_work(tmp_path, "yt-1", [auto("mapping", suggested="отображение"), auto("modifier")],
                      [("A mapping and a modifier.", "Отображение и модификатор.")])
    two = lesson_work(tmp_path, "yt-2", [auto("mapping", suggested="маппинг"), auto("event"),
                                         auto("modifier")],
                      [("The mapping, the mapping, the event.", "Маппинг, маппинг, событие."),
                       ("Another event and an event.", "Ещё событие.")])
    cg.after_lesson(course, "01 One", 1, one, "ru")
    cg.after_lesson(course, "01 One", 1, one, "ru")  # a re-run: not counted twice
    cg.after_lesson(course, "02 Two", 2, two, "ru")
    glossary = load_glossary(cg.course_file(course))
    assert [e.term for e in glossary.entries] == ["mapping", "event"]  # 3 and 3 occurrences; modifier 2
    assert glossary.find("mapping").translations == {"ru": "отображение"}  # the first lesson's rendering


def test_each_lesson_keeps_the_glossary_it_ran_with(tmp_path):
    course = tmp_path / "course"
    course.mkdir()
    assert cg.inputs_for(course, "02 Two") == []  # nothing yet: the first lesson runs without it
    cg.course_file(course).write_text("| term | keep |\n|---|---|\n| event | no |\n", encoding="utf-8")
    assert cg.inputs_for(course, "02 Two") == [cg.course_file(course)]  # an estimate reads it, writes nothing
    snapshot = cg.inputs_for(course, "02 Two", write=True)[0]
    assert snapshot.parent.name == youtube.SERVICE_DIR
    assert snapshot.read_text(encoding="utf-8").endswith("no |\n")
    cg.course_file(course).write_text("| term | keep |\n|---|---|\n| other | no |\n", encoding="utf-8")
    assert cg.inputs_for(course, "02 Two") == [snapshot]  # a re-run: the same input, so the cache holds
    assert "event" in snapshot.read_text(encoding="utf-8")


def test_the_domain_dictionary_wins_over_the_course_glossary(tmp_path):
    course_file = tmp_path / "course-glossary.md"
    course_file.write_text(to_markdown(Glossary(entries=[
        GlossaryEntry(term="gas price", keep=False, translations={"ru": "стоимость газа"}),
        GlossaryEntry(term="storage slot", keep=False, translations={"ru": "слот хранилища"})])),
        encoding="utf-8")
    paths = user_glossary.user_glossary_paths(None, "web3") + [course_file]
    combined = load_glossary(user_glossary.run_glossary(paths, tmp_path / "work"))
    assert combined.find("gas price").translations["ru"] == "цена газа"  # glossaries/web3.md
    assert combined.find("storage slot").translations["ru"] == "слот хранилища"  # only in the course's


def test_count_does_not_match_inside_longer_words():
    assert cg.count("event", "An event, events and the Event.") == 2
    assert cg.count("msg.sender", "Use msg.sender here.") == 1
    assert cg.count("sender", "Use msg.sender here.") == 0


def test_a_versioned_dictionary_is_found_by_its_dotted_name():
    from talk_autopsy import config
    from talk_autopsy.glossary import GlossaryError
    assert user_glossary.user_glossary_paths(None, "web3-v2.1") == [config.GLOSSARIES_DIR / "web3-v2.1.md"]
    assert user_glossary.user_glossary_paths(None, "web3-v2") == [config.GLOSSARIES_DIR / "web3-v2.md"]
    for bad in ("../web3", "web3.md", "web3-v2.9"):
        try:
            user_glossary.user_glossary_paths(None, bad)
        except GlossaryError:
            continue
        raise AssertionError(bad)


def test_the_pages_smart_contract_option_uses_the_chosen_dictionary(tmp_path, monkeypatch):
    from talk_autopsy.web import jobs
    from talk_autopsy.web import settings as web_settings
    core = jobs.Core()
    assert core.user_glossary("web3", tmp_path).name == "web3.md"  # the current default
    monkeypatch.setattr(web_settings, "WEB3_DICTIONARY", "web3-v2.1")
    assert core.user_glossary("web3", tmp_path).name == "web3-v2.1.md"
    assert core.user_glossary("none", tmp_path) is None
