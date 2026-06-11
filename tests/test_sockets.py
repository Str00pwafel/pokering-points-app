"""Socket.IO handler tests: join/vote/reveal flow, reconnect preservation,
host transfer, reconnect-token rejection, vote validation, username dedupe,
and rate-limit accounting."""

import asyncio

import app.sockets as sockets
import app.state as state
from app.state import sessions, socket_rate_limits
from tests.conftest import CLIENT_A, CLIENT_B, CLIENT_C, SESSION_ID, join, make_session


def stored_token(client_id: str, session_id: str = SESSION_ID) -> str:
    return state.reconnect_tokens[(session_id, client_id)]


def user_by_client(client_id: str, session_id: str = SESSION_ID) -> dict | None:
    for user in sessions[session_id]["users"].values():
        if user["clientId"] == client_id:
            return user
    return None


class TestJoin:
    async def test_first_joiner_becomes_host(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        user = user_by_client(CLIENT_A)
        assert user is not None
        assert user["isHost"] is True
        assert sessions[SESSION_ID]["hostClientId"] == CLIENT_A

    async def test_second_joiner_is_not_host(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        assert user_by_client(CLIENT_B)["isHost"] is False

    async def test_join_unknown_session_fails(self, emits):
        await join("sid1", CLIENT_A, "Alice")
        assert emits.last("joinFailed")["reason"] == "Session not found"

    async def test_invalid_username_fails(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "<script>")
        assert "Invalid username" in emits.last("joinFailed")["reason"]

    async def test_new_client_receives_reconnect_token(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        token_payload = emits.last("reconnectToken")
        assert token_payload is not None
        assert token_payload["token"] == stored_token(CLIENT_A)

    async def test_duplicate_username_suffixed(self, emits):
        # BUG-8: two users with the same name were indistinguishable in the
        # user list, toasts, and revealed votes.
        make_session()
        await join("sid1", CLIENT_A, "Rinaldo")
        await join("sid2", CLIENT_B, "Rinaldo")
        await join("sid3", CLIENT_C, "rinaldo")
        assert user_by_client(CLIENT_A)["username"] == "Rinaldo"
        assert user_by_client(CLIENT_B)["username"] == "Rinaldo-2"
        assert user_by_client(CLIENT_C)["username"] == "rinaldo-3"

    async def test_rename_to_own_name_not_suffixed(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid1b", CLIENT_A, "Alice", reconnectToken=stored_token(CLIENT_A))
        assert user_by_client(CLIENT_A)["username"] == "Alice"


class TestReconnect:
    async def test_wrong_token_rejected(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_A, "Alice", reconnectToken="forged-token")
        assert emits.last("joinFailed")["reason"] == "Invalid reconnect token"
        # original socket still holds the seat
        assert "sid1" in sessions[SESSION_ID]["users"]
        assert "sid2" not in sessions[SESSION_ID]["users"]

    async def test_reconnect_within_grace_preserves_vote_and_role(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 5})
        assert user_by_client(CLIENT_A)["vote"] == 5

        await sockets.disconnect("sid1")
        assert user_by_client(CLIENT_A) is None  # gone, leave pending

        await join("sid1b", CLIENT_A, "Alice", reconnectToken=stored_token(CLIENT_A))
        rejoined = user_by_client(CLIENT_A)
        assert rejoined["vote"] == 5
        assert rejoined["isHost"] is True
        # the pending delayed-leave was cancelled
        assert (SESSION_ID, CLIENT_A) not in sockets._pending_leave_tasks

    async def test_auto_host_transfer_after_grace(self, emits, monkeypatch):
        monkeypatch.setattr(sockets, "RECONNECT_GRACE", 0)
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")

        await sockets.disconnect("sid1")
        pending = sockets._pending_leave_tasks.get((SESSION_ID, CLIENT_A))
        assert pending is not None
        await pending

        assert user_by_client(CLIENT_B)["isHost"] is True
        assert sessions[SESSION_ID]["hostClientId"] == CLIENT_B
        transferred = emits.last("hostTransferred")
        assert transferred["reason"] == "auto"
        assert transferred["clientId"] == CLIENT_B

    async def test_host_left_emitted_when_no_candidates(self, emits, monkeypatch):
        monkeypatch.setattr(sockets, "RECONNECT_GRACE", 0)
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await sockets.disconnect("sid1")
        await sockets._pending_leave_tasks[(SESSION_ID, CLIENT_A)]
        assert emits.events("hostLeft")


class TestVoteFlow:
    async def test_vote_recorded_and_value_not_broadcast(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 8})

        assert user_by_client(CLIENT_A)["vote"] == 8
        # the room broadcast carries presence only, never the raw value
        voted_evt = emits.last("userVoted")
        assert voted_evt["clientId"] == CLIENT_A
        assert "vote" not in voted_evt
        # the private selfState echo does carry the value
        assert emits.last("selfState")["vote"] == 8

    async def test_all_voted_triggers_countdown_and_reveal(self, emits, fast_countdown):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 5})
        assert not sessions[SESSION_ID]["revealed"]

        await sockets.vote("sid2", {"sessionId": SESSION_ID, "value": 8})
        session = sessions[SESSION_ID]
        assert session["revealed"] is True
        await session["countdownTask"]

        assert session["countdownActive"] is False
        reveal = emits.last("revealVotes")
        assert reveal["stats"]["average"] == 6.5
        assert reveal["stats"]["median"] in (5, 8)
        assert reveal["stats"]["consensus"] is False
        votes = {u["clientId"]: u["vote"] for u in reveal["users"]}
        assert votes[CLIENT_A] == 5 and votes[CLIENT_B] == 8

    async def test_consensus_detected(self, emits, fast_countdown):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 3})
        await sockets.vote("sid2", {"sessionId": SESSION_ID, "value": 3})
        await sessions[SESSION_ID]["countdownTask"]
        assert emits.last("revealVotes")["stats"]["consensus"] is True

    async def test_crashed_countdown_does_not_lock_session(
        self, emits, fast_countdown, monkeypatch
    ):
        # BUG-3: an unexpected exception in the countdown left countdownActive
        # set forever, so requestNewRound dead-ended until idle timeout.
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")

        def boom(_session):
            raise RuntimeError("stats regression")

        monkeypatch.setattr(sockets, "_compute_vote_stats", boom)
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 5})
        await sockets.vote("sid2", {"sessionId": SESSION_ID, "value": 8})
        await sessions[SESSION_ID]["countdownTask"]

        session = sessions[SESSION_ID]
        assert session["countdownActive"] is False
        assert state.countdown_active == 0
        # host can start a new round again
        await sockets.requestNewRound("sid1", {"sessionId": SESSION_ID})
        assert session["revealed"] is False
        assert session["roundCount"] == 2

    async def test_spectator_cannot_vote(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob", isSpectator=True)
        await sockets.vote("sid2", {"sessionId": SESSION_ID, "value": 5})
        assert user_by_client(CLIENT_B)["vote"] is None

    async def test_vote_change_limited_to_once(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 5})
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 8})
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 13})
        assert user_by_client(CLIENT_A)["vote"] == 8  # third attempt rejected


class TestVoteValidation:
    async def setup_voters(self):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")

    async def test_non_integral_float_rejected(self, emits):
        # BUG-5: 2.9 was silently truncated and recorded as vote 2.
        await self.setup_voters()
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 2.9})
        assert user_by_client(CLIENT_A)["vote"] is None
        assert emits.last("actionFailed")["reason"] == "Invalid vote"

    async def test_integral_float_accepted(self, emits):
        await self.setup_voters()
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 2.0})
        assert user_by_client(CLIENT_A)["vote"] == 2

    async def test_bool_rejected(self, emits):
        await self.setup_voters()
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": True})
        assert user_by_client(CLIENT_A)["vote"] is None

    async def test_non_finite_float_rejected(self, emits):
        await self.setup_voters()
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": float("inf")})
        assert user_by_client(CLIENT_A)["vote"] is None

    async def test_value_not_in_deck_rejected(self, emits):
        await self.setup_voters()
        await sockets.vote("sid1", {"sessionId": SESSION_ID, "value": 4})  # fibonacci deck
        assert user_by_client(CLIENT_A)["vote"] is None


class TestHostTransfer:
    async def test_manual_transfer(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.transferHost("sid1", {"sessionId": SESSION_ID, "clientId": CLIENT_B})
        assert user_by_client(CLIENT_B)["isHost"] is True
        assert user_by_client(CLIENT_A)["isHost"] is False
        assert emits.last("hostTransferred")["reason"] == "manual"

    async def test_non_host_cannot_transfer(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.transferHost("sid2", {"sessionId": SESSION_ID, "clientId": CLIENT_B})
        assert user_by_client(CLIENT_A)["isHost"] is True

    async def test_cannot_transfer_to_spectator(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob", isSpectator=True)
        await sockets.transferHost("sid1", {"sessionId": SESSION_ID, "clientId": CLIENT_B})
        assert user_by_client(CLIENT_A)["isHost"] is True
        assert "active voter" in emits.last("actionFailed")["reason"]


class TestNewRound:
    async def test_new_round_applies_requested_deck(self, emits):
        # The post-reveal deck picker queues a deckType that rides on
        # requestNewRound — pin that server contract.
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await sockets.requestNewRound("sid1", {"sessionId": SESSION_ID, "deckType": "tshirt"})
        assert sessions[SESSION_ID]["deckType"] == "tshirt"
        assert "XL" in sessions[SESSION_ID]["deck"]
        reset = emits.last("roundReset")
        assert reset["deckType"] == "tshirt"

    async def test_invalid_deck_falls_back_to_default(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await sockets.requestNewRound("sid1", {"sessionId": SESSION_ID, "deckType": "bogus"})
        assert sessions[SESSION_ID]["deckType"] == "fibonacci"


class TestNewRoundRateAccounting:
    async def test_non_host_request_does_not_consume_budget(self, emits):
        # BUG-4: the rate-limit hit was counted before the host check, so
        # non-host requests burned the host's shared 30/hour budget.
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")
        await sockets.requestNewRound("sid2", {"sessionId": SESSION_ID})
        assert emits.last("actionFailed")["reason"] == "Only host can request new round"
        for bucket in socket_rate_limits.values():
            assert not bucket.get("requestNewRound")

    async def test_host_request_counts_and_resets_round(self, emits):
        make_session()
        await join("sid1", CLIENT_A, "Alice")
        await sockets.requestNewRound("sid1", {"sessionId": SESSION_ID})
        assert sessions[SESSION_ID]["roundCount"] == 2
        assert any(bucket.get("requestNewRound") for bucket in socket_rate_limits.values())


class TestRateLimitKeying:
    async def test_same_ip_different_clients_use_separate_buckets(self, emits):
        # BUG-2: limits were keyed per IP, so a shared NAT pooled everyone.
        make_session()
        state.socket_ip_map["sid1"] = "203.0.113.7"
        state.socket_ip_map["sid2"] = "203.0.113.7"
        await join("sid1", CLIENT_A, "Alice")
        await join("sid2", CLIENT_B, "Bob")

        from app.rate_limit import check_socket_rate_limit

        assert check_socket_rate_limit("sid1", "x", limit=1, window=60) is True
        assert check_socket_rate_limit("sid1", "x", limit=1, window=60) is False
        # same IP, different clientId — own bucket, not exhausted by sid1
        assert check_socket_rate_limit("sid2", "x", limit=1, window=60) is True

    async def test_same_client_new_socket_shares_bucket(self, emits):
        make_session()
        state.socket_ip_map["sid1"] = "203.0.113.7"
        await join("sid1", CLIENT_A, "Alice")

        from app.rate_limit import check_socket_rate_limit

        assert check_socket_rate_limit("sid1", "x", limit=1, window=60) is True
        # reconnect: new sid, same ip + clientId → same bucket
        await sockets.disconnect("sid1")
        state.socket_ip_map["sid1b"] = "203.0.113.7"
        await join("sid1b", CLIENT_A, "Alice", reconnectToken=stored_token(CLIENT_A))
        assert check_socket_rate_limit("sid1b", "x", limit=1, window=60) is False


class TestCleanupResilience:
    async def test_session_cleanup_survives_bad_session_shape(self, emits, monkeypatch):
        # IMP-10: one malformed session must not kill the cleanup task.
        import app.state as st

        make_session()
        sessions["B" * 16] = {"users": {}}  # missing createdAt/lastActivity

        sleeps = iter([0])

        async def one_shot_sleep(_s):
            try:
                next(sleeps)
            except StopIteration:
                raise asyncio.CancelledError from None

        monkeypatch.setattr(asyncio, "sleep", one_shot_sleep)
        try:
            await st.session_cleanup()
        except asyncio.CancelledError:
            pass
        # malformed session removed, healthy session retained
        assert "B" * 16 not in sessions
        assert SESSION_ID in sessions
