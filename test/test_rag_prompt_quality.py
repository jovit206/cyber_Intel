from backend.services.vector_rag import build_rag_prompt, validate_grounded_answer


def test_rag_prompt_bounds_each_source_and_recent_conversation_context():
    sources = [{"source": "feed", "chunk_id": f"attributes:{index}", "text": "x" * 4000} for index in range(5)]
    memory = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "y" * 4000}
        for index in range(10)
    ]

    system, user = build_rag_prompt("current question", sources, memory)

    assert system.count("x" * 1200) == 5
    assert system.count("Excerpt shortened") == 5
    assert user.count("y" * 600) == 6
    assert user.count("Excerpt shortened") == 6
    assert "current question" in user


def test_citation_only_llm_response_is_rejected():
    assert validate_grounded_answer("[S1]\n[S2]", [{"text": "source evidence one"}, {"text": "source evidence two"}]) == "citation_only_answer"


def test_substantive_cited_response_remains_accepted():
    assert validate_grounded_answer("The report describes the observed activity. [S1]", [{"text": "observed activity"}]) is None

