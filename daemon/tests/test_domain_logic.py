import pytest
import json
import forcefocus_daemon

def test_expand_youtube_in_blacklist(mock_daemon):
    mock_daemon.domains_manager.save_lists({"blacklist": ["youtube.com"], "whitelist": []})
    mock_daemon._atomic_write_json(forcefocus_daemon.GROUPS_FILE, {})
    
    domains = mock_daemon.domains_manager.get_blacklist_domains([])
    assert "youtube.com" in domains

def test_get_blacklist_with_groups(mock_daemon):
    mock_daemon.domains_manager.save_lists({"blacklist": ["base.com"], "whitelist": []})
    mock_daemon._atomic_write_json(forcefocus_daemon.GROUPS_FILE, {
        "work": ["work-distraction.com"]
    })
    
    domains = mock_daemon.domains_manager.get_blacklist_domains(["work"])
    assert "base.com" in domains
    assert "work-distraction.com" in domains


def test_context_menu_blacklist_domain_is_saved_when_idle(mock_daemon):
    before_revision = mock_daemon.state_revision
    response = mock_daemon.domains_manager.cmd_add_domain(
        {"list": "blacklist", "domain": "https://www.example.com/article"}
    )

    assert response["status"] == "ok"
    assert "example.com" in response["lists"]["blacklist"]
    assert mock_daemon.state_revision == before_revision + 1
    assert mock_daemon.session_manager.cmd_get_status()["state_revision"] == before_revision + 1
