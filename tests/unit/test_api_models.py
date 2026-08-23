from knowledgeforge.api import AskRequest


def test_ask_request_rejects_empty_question() -> None:
    try:
        AskRequest(question="")
    except ValueError:
        return
    raise AssertionError("empty questions must be rejected")
