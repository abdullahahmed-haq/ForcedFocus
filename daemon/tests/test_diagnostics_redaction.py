from cli.commands.diagnostics import redact


def test_diagnostics_redacts_secrets_domains_coordinates_and_users():
    raw = (
        "api_token=0123456789abcdef0123456789abcdef\n"
        "blocked example.com at 31.11341311 for /Users/alice/work\n"
        "intent=Finish private proposal\n"
    )

    cleaned = redact(raw)

    assert "0123456789abcdef" not in cleaned
    assert "example.com" not in cleaned
    assert "31.11341311" not in cleaned
    assert "/Users/alice" not in cleaned
    assert "private proposal" not in cleaned
