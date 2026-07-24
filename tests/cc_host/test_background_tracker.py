from trowel_py.cc_host.background_tracker import BackgroundActivityTracker


class TestRegistration:
    def test_register_started_makes_task_pending(self):
        t = BackgroundActivityTracker()
        assert not t.has_pending_tasks()
        t.register_started("b7cgk2tn3", "call_bg1", "local_bash")
        assert t.has_pending_tasks()
        assert t.pending_ids() == frozenset({"b7cgk2tn3"})

    def test_register_started_ignores_empty_task_id(self):
        t = BackgroundActivityTracker()
        t.register_started("", "call_x", "local_bash")
        assert not t.has_pending_tasks()

    def test_multiple_tasks_independent(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        t.register_started("B", "call_b", "local_agent")
        assert t.pending_ids() == frozenset({"A", "B"})


class TestTermination:
    def test_terminate_removes_pending(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        assert t.terminate("A") is True
        assert not t.has_pending_tasks()

    def test_duplicate_terminate_is_idempotent(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        assert t.terminate("A") is True
        assert t.terminate("A") is False
        assert not t.has_pending_tasks()

    def test_terminate_unknown_task_no_state_change(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        assert t.terminate("unknown") is False
        assert t.pending_ids() == frozenset({"A"}), (
            "unknown task notification must not touch other pending tasks"
        )

    def test_partial_termination_keeps_others_pending(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        t.register_started("B", "call_b", "local_bash")
        t.terminate("A")
        assert t.has_pending_tasks()
        assert t.pending_ids() == frozenset({"B"})
        t.terminate("B")
        assert not t.has_pending_tasks()

    def test_terminate_does_not_depend_on_status_value(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        assert t.terminate("A") is True
        t.register_started("B", "call_b", "local_agent")
        assert t.terminate("B") is True


class TestProgress:
    def test_mark_progress_keeps_known_task_pending(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        t.mark_progress("A")
        assert t.has_pending_tasks()
        assert t.pending_ids() == frozenset({"A"})

    def test_mark_progress_for_unknown_task_no_invent(self):
        t = BackgroundActivityTracker()
        t.mark_progress("orphan")
        assert not t.has_pending_tasks()


class TestReset:
    def test_reset_clears_all_pending(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        t.register_started("B", "call_b", "local_bash")
        t.reset()
        assert not t.has_pending_tasks()
        assert t.pending_ids() == frozenset()

    def test_pending_ids_is_snapshot(self):
        t = BackgroundActivityTracker()
        t.register_started("A", "call_a", "local_bash")
        snap = t.pending_ids()
        t.register_started("B", "call_b", "local_bash")
        assert snap == frozenset({"A"})
        assert t.pending_ids() == frozenset({"A", "B"})
