"""Source-level contract checks for the authenticated AI Threat Assistant entry point."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
CHAT = (ROOT / "public" / "ai-threat-assistant.js").read_text(encoding="utf-8")
CHAT_CSS = (ROOT / "public" / "ai-threat-assistant.css").read_text(encoding="utf-8")
MAIN_CSS = (ROOT / "public" / "style.css").read_text(encoding="utf-8")


def test_assistant_navigation_targets_existing_chat_workspace():
    assert HTML.count("data-ai-threat-assistant") == 1
    assert 'data-view="ai-assistant"' in HTML
    assert 'aria-controls="view-ai-assistant"' in HTML
    assert HTML.count('id="view-ai-assistant"') == 1
    assert HTML.count('data-ai-chat-root') == 1
    assert 'AI Threat Assistant' in HTML
    assert 'Your intelligent cybersecurity research assistant.' in HTML
    assert 'Ask anything about cybersecurity...' in HTML
    for prompt in (
        'Analyse a CVE vulnerability', 'Investigate a suspicious IP address',
        'Explain recent cybersecurity threats', 'Recommend mitigation strategies',
    ):
        assert prompt in HTML
    assistant_view = HTML.split('id="view-ai-assistant"', 1)[1].split('</section>', 1)[0]
    for removed in ('AI RESEARCH / LIVE CAPABILITIES', 'Embedding provider', 'LLM Provider', 'MongoDB Vector Search', 'Vector Search', 'Benchmarking'):
        assert removed not in assistant_view


def test_assistant_navigation_requires_a_signed_in_profile_and_restores_history():
    assert "if (assistantLink) assistantLink.hidden = !data?.user;" in APP
    assert "requestedView.get('view') === 'ai-assistant'" in APP
    assert 'document.getElementById(`view-${viewId}`)' in APP
    assert 'window.initializeAiThreatAssistant?.();' in APP
    assert "params.set('view', 'ai-assistant');" in APP
    assert "params.get('view') === 'ai-assistant'" in APP
    assert "!restoringHistory" in APP


def test_existing_mobile_navigation_layout_contains_assistant_item():
    assert ".sidebar-nav {\n    flex-direction: row;\n    flex-wrap: wrap;" in MAIN_CSS
    assert ".nav-link[hidden]" in MAIN_CSS
    assert "@media (max-width: 760px)" in CHAT_CSS
    assert ".is-history-open .ai-chat-sidebar" in CHAT_CSS
    assert "data-ai-history-toggle" in CHAT
    assert "data-ai-new-chat" in CHAT


def test_chatbot_keeps_authenticated_history_filters_sources_and_keyboard_behavior():
    for endpoint in ("/api/ask", "/api/ask/history?limit=30", "/api/ask/history/${encodeURIComponent(selectedConversation)}"):
        assert endpoint in CHAT
    assert "credentials: 'same-origin'" in CHAT
    assert "conversation_id: selectedConversation" in CHAT
    assert "threat_status: root.querySelector('[data-ai-threat-status]').value || null" in CHAT
    assert "source_chunk_ids" in CHAT
    assert "[S${index + 1}]" in CHAT
    assert "event.key === 'Enter' && !event.shiftKey" in CHAT
    assert 'role="status"' in CHAT

