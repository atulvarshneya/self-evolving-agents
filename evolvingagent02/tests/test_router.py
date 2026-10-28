from evolvingagent02 import router
from replies import route


def test_a_message_with_a_report_is_a_task_without_asking_the_llm(fake_llm):
    assert router.route_message("Review this.", has_report=True) == "task"
    assert fake_llm.calls == []


def test_routes_come_from_the_llm(fake_llm):
    fake_llm.responses = [route("feedback"), route("task")]

    assert router.route_message("That should have been flagged.", has_report=False) == "feedback"
    assert router.route_message("What's our dinner policy?", has_report=False) == "task"
    assert "That should have been flagged." in fake_llm.calls[0][1]


def test_retries_an_unreadable_reply(fake_llm):
    fake_llm.responses = ["not json", route("maybe"), route("feedback")]

    assert router.route_message("From now on, deny alcohol.", has_report=False) == "feedback"
    assert len(fake_llm.calls) == 3


def test_falls_back_to_task_after_the_retries(fake_llm):
    fake_llm.responses = ["not json"] * (router.MAX_CALL_RETRIES + 1)

    assert router.route_message("???", has_report=False) == "task"
    assert fake_llm.responses == []
