"""Pure-function tests: input validation, proxy parsing, rate-limit helpers,
maintenance scheduling (regression coverage for BUG-1), and audit IP masking."""

from datetime import datetime

from app.config import sanitize_username
from app.logging_setup import mask_ip
from app.rate_limit import _bound_dict, _is_peer_trusted, _pick_forwarded_hop
from app.routes import _next_maintenance_start


class TestSanitizeUsername:
    def test_valid_plain(self):
        assert sanitize_username("Rinaldo") == "Rinaldo"

    def test_strips_control_chars(self):
        assert sanitize_username("Rin\x00aldo\u200b") == "Rinaldo"

    def test_trims_whitespace(self):
        assert sanitize_username("  Bob  ") == "Bob"

    def test_unicode_letters_allowed(self):
        assert sanitize_username("José Müller") == "José Müller"

    def test_rejects_non_string(self):
        assert sanitize_username(42) is None
        assert sanitize_username(None) is None

    def test_rejects_empty_after_cleaning(self):
        assert sanitize_username("\x00\x1f ") is None

    def test_rejects_too_long(self):
        assert sanitize_username("a" * 31) is None
        assert sanitize_username("a" * 30) == "a" * 30

    def test_rejects_disallowed_chars(self):
        assert sanitize_username("bob<script>") is None
        assert sanitize_username("a(b)") is None


class TestPickForwardedHop:
    def test_single_hop(self):
        assert _pick_forwarded_hop("1.2.3.4") == "1.2.3.4"

    def test_depth_one_takes_rightmost(self, monkeypatch):
        import app.rate_limit as rl

        monkeypatch.setattr(rl, "PROXY_DEPTH", 1)
        assert _pick_forwarded_hop("9.9.9.9, 1.2.3.4") == "1.2.3.4"

    def test_depth_two_takes_second_from_right(self, monkeypatch):
        import app.rate_limit as rl

        monkeypatch.setattr(rl, "PROXY_DEPTH", 2)
        assert _pick_forwarded_hop("9.9.9.9, 1.2.3.4, 5.6.7.8") == "1.2.3.4"

    def test_depth_exceeding_hops_clamps_to_leftmost(self, monkeypatch):
        import app.rate_limit as rl

        monkeypatch.setattr(rl, "PROXY_DEPTH", 5)
        assert _pick_forwarded_hop("9.9.9.9, 1.2.3.4") == "9.9.9.9"

    def test_empty_returns_none(self):
        assert _pick_forwarded_hop("") is None
        assert _pick_forwarded_hop(" , ") is None


class TestIsPeerTrusted:
    def test_none_peer_never_trusted(self):
        assert _is_peer_trusted(None) is False

    def test_empty_allowlist_trusts_all(self, monkeypatch):
        import app.rate_limit as rl

        monkeypatch.setattr(rl, "TRUSTED_PROXY_IPS", [])
        assert _is_peer_trusted("203.0.113.7") is True

    def test_allowlist_match(self, monkeypatch):
        import ipaddress

        import app.rate_limit as rl

        monkeypatch.setattr(rl, "TRUSTED_PROXY_IPS", [ipaddress.ip_network("10.0.0.0/8")])
        assert _is_peer_trusted("10.1.2.3") is True
        assert _is_peer_trusted("192.168.1.1") is False

    def test_invalid_ip_not_trusted(self, monkeypatch):
        import ipaddress

        import app.rate_limit as rl

        monkeypatch.setattr(rl, "TRUSTED_PROXY_IPS", [ipaddress.ip_network("10.0.0.0/8")])
        assert _is_peer_trusted("not-an-ip") is False


class TestBoundDict:
    def test_evicts_oldest_first(self):
        d = {f"k{i}": i for i in range(10)}
        _bound_dict(d, max_entries=3)
        assert list(d) == ["k7", "k8", "k9"]

    def test_no_eviction_under_cap(self):
        d = {"a": 1}
        _bound_dict(d, max_entries=3)
        assert d == {"a": 1}


class TestNextMaintenanceStart:
    BASE = {"enabled": True, "timezone": "Europe/Amsterdam", "at": "", "startsAt": ""}

    def test_disabled_returns_none(self):
        assert _next_maintenance_start({**self.BASE, "enabled": False, "at": "21:00"}) is None

    def test_no_schedule_returns_none(self):
        assert _next_maintenance_start(self.BASE) is None

    def test_valid_daily_time(self):
        result = _next_maintenance_start({**self.BASE, "at": "21:00"})
        assert isinstance(result, datetime)
        assert (result.hour, result.minute) == (21, 0)

    def test_valid_starts_at_iso(self):
        result = _next_maintenance_start({**self.BASE, "startsAt": "2026-06-08T21:00:00+02:00"})
        assert isinstance(result, datetime)
        assert result.isoformat().startswith("2026-06-08T21:00")

    def test_starts_at_zulu_suffix(self):
        result = _next_maintenance_start({**self.BASE, "startsAt": "2026-06-08T19:00:00Z"})
        assert isinstance(result, datetime)

    # BUG-1 regression: these raised AttributeError (ZoneInfo.KeyError does not
    # exist) and turned any schedule typo into a 500 on /maintenance.
    def test_invalid_timezone_returns_none(self):
        assert (
            _next_maintenance_start({**self.BASE, "at": "21:00", "timezone": "Not/AZone"}) is None
        )

    def test_out_of_range_time_returns_none(self):
        assert _next_maintenance_start({**self.BASE, "at": "25:99"}) is None

    def test_non_numeric_time_returns_none(self):
        assert _next_maintenance_start({**self.BASE, "at": "7pm"}) is None

    def test_malformed_starts_at_returns_none(self):
        assert _next_maintenance_start({**self.BASE, "startsAt": "tomorrow-ish"}) is None


class TestMaskIp:
    def test_ipv4_truncated(self):
        assert mask_ip("10.1.2.3") == "10.1.x.x"

    def test_ipv6_truncated(self):
        assert mask_ip("2001:db8::1") == "2001:0db8:x:x:x:x:x:x"

    def test_non_ip_passthrough(self):
        assert mask_ip("testclient") == "testclient"

    def test_non_string_passthrough(self):
        assert mask_ip(None) is None
        assert mask_ip(7) == 7
