import pytest
import json
import forcefocus_daemon
from unittest.mock import MagicMock

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


def test_list_edit_during_active_session_updates_only_the_next_session(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.active_domains = ["snapshot.example"]
    mock_daemon.session_base_domains = ["snapshot.example"]

    response = mock_daemon.domains_manager.cmd_add_domain(
        {"list": "blacklist", "domain": "next-session.example"}
    )

    assert response["status"] == "ok"
    assert "next-session.example" in response["lists"]["blacklist"]
    assert mock_daemon.state.active_domains == ["snapshot.example"]
    assert mock_daemon.session_base_domains == ["snapshot.example"]


def test_empty_blacklist_produces_no_blocked_domains(mock_daemon):
    mock_daemon.domains_manager.save_lists({"blacklist": [], "whitelist": []})

    domains = mock_daemon.domains_manager.get_blacklist_domains([])

    assert domains == []


def test_list_cache_reflects_sequential_mutations(mock_daemon):
    mock_daemon.domains_manager.save_lists({"blacklist": ["first.example"], "whitelist": []})
    assert mock_daemon.domains_manager.load_lists()["blacklist"] == ["first.example"]

    mock_daemon.domains_manager.cmd_add_domain(
        {"list": "blacklist", "domain": "second.example"}
    )
    mock_daemon.domains_manager.cmd_add_domain(
        {"list": "blacklist", "domain": "third.example"}
    )
    mock_daemon.domains_manager.cmd_remove_domain(
        {"list": "blacklist", "domain": "second.example"}
    )

    assert mock_daemon.domains_manager.load_lists()["blacklist"] == [
        "first.example",
        "third.example",
    ]


def test_empty_whitelist_has_no_implicit_infrastructure_allowances(mock_daemon):
    assert mock_daemon.domains_manager.expand_whitelist_domains([]) == []


def test_permanent_block_add_rolls_back_when_persistence_fails(mock_daemon):
    mock_daemon._atomic_write_json = MagicMock(side_effect=OSError("disk full"))
    mock_daemon.events.emit = MagicMock()

    response = mock_daemon.domains_manager.cmd_add_perma_block(
        {"domain": "example.com"}
    )

    assert response["status"] == "error"
    assert mock_daemon.perma_blocklist == []
    mock_daemon.events.emit.assert_not_called()
