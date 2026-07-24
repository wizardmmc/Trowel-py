from trowel_py.cc_host.stalled import StalledDetector


def _detector() -> StalledDetector:
    return StalledDetector(
        threshold_mild=120.0, threshold_severe=300.0, threshold_kill=1800.0
    )


class TestPhase:
    def test_quiet_before_mild(self):
        d = _detector()
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.phase(now=119.0) == "quiet"
        assert d.phase(now=120.0) == "mild"

    def test_mild_to_severe(self):
        d = _detector()
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.phase(now=299.0) == "mild"
        assert d.phase(now=300.0) == "severe"

    def test_severe_to_kill(self):
        d = _detector()
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.phase(now=1799.0) == "severe"
        assert d.phase(now=1800.0) == "kill"

    def test_any_event_resets_to_quiet(self):
        d = _detector()
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.phase(now=150.0) == "mild"
        d.record_event(now=150.0)
        assert d.phase(now=200.0) == "quiet"

    def test_quiet_inside_retry_backoff(self):
        d = _detector()
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        d.record_retry(now=10.0, retry_delay_ms=240_000)
        assert d.phase(now=200.0) == "quiet"
        assert d.phase(now=260.0) == "mild"

    def test_not_started_is_quiet(self):
        d = _detector()
        assert d.phase(now=1000.0) == "quiet"

    def test_start_turn_resets(self):
        d = _detector()
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.phase(now=150.0) == "mild"
        d.start_turn(now=200.0)
        assert d.phase(now=250.0) == "quiet"

    def test_quiet_seconds_reports_silent_duration(self):
        d = _detector()
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.quiet_seconds(now=150.0) == 150.0
