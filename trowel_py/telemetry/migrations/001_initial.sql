CREATE TABLE telemetry_batches (
    batch_id TEXT PRIMARY KEY NOT NULL,
    fingerprint BLOB NOT NULL CHECK(length(fingerprint) = 32),
    schema_version INTEGER NOT NULL,
    source_component TEXT NOT NULL,
    collected_at_ns INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('normal', 'backfill')),
    accepted_count INTEGER NOT NULL CHECK(accepted_count >= 0),
    rejected_count INTEGER NOT NULL CHECK(rejected_count >= 0),
    created_at_ns INTEGER NOT NULL
);

CREATE TABLE raw_spans (
    trace_id BLOB NOT NULL CHECK(length(trace_id) = 16),
    span_id BLOB NOT NULL CHECK(length(span_id) = 8),
    parent_span_id BLOB CHECK(parent_span_id IS NULL OR length(parent_span_id) = 8),
    started_at_ns INTEGER NOT NULL,
    hour_start_ns INTEGER NOT NULL,
    ended_at_ns INTEGER NOT NULL,
    duration_ms REAL NOT NULL CHECK(duration_ms >= 0),
    duration_bucket INTEGER NOT NULL CHECK(duration_bucket BETWEEN 0 AND 14),
    component TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime TEXT NOT NULL,
    model TEXT NOT NULL,
    session_ref BLOB CHECK(session_ref IS NULL OR length(session_ref) = 16),
    call_ref BLOB CHECK(call_ref IS NULL OR length(call_ref) = 16),
    attributes_json TEXT NOT NULL,
    PRIMARY KEY(span_id)
) WITHOUT ROWID;

CREATE INDEX raw_spans_started_idx ON raw_spans(started_at_ns DESC);
CREATE INDEX raw_spans_trace_idx ON raw_spans(trace_id, started_at_ns);
CREATE INDEX raw_spans_operation_idx
    ON raw_spans(operation, status, started_at_ns);
CREATE INDEX raw_spans_session_idx ON raw_spans(session_ref, started_at_ns);
CREATE INDEX raw_spans_aggregation_idx ON raw_spans(
    hour_start_ns, component, operation, status, runtime, model,
    duration_bucket, duration_ms
);

CREATE TABLE span_links (
    span_id BLOB NOT NULL,
    link_index INTEGER NOT NULL CHECK(link_index >= 0),
    linked_trace_id BLOB NOT NULL CHECK(length(linked_trace_id) = 16),
    linked_span_id BLOB CHECK(linked_span_id IS NULL OR length(linked_span_id) = 8),
    PRIMARY KEY(span_id, link_index)
) WITHOUT ROWID;

CREATE TABLE raw_metrics (
    metric_key BLOB PRIMARY KEY NOT NULL CHECK(length(metric_key) = 16),
    observed_at_ns INTEGER NOT NULL,
    component TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    unit TEXT NOT NULL,
    value REAL NOT NULL CHECK(value >= 0),
    status TEXT NOT NULL,
    runtime TEXT,
    model TEXT,
    operation TEXT,
    attributes_json TEXT NOT NULL
) WITHOUT ROWID;

CREATE INDEX raw_metrics_observed_idx ON raw_metrics(observed_at_ns DESC);
CREATE INDEX raw_metrics_name_idx ON raw_metrics(name, observed_at_ns);

CREATE TABLE hourly_span_stats (
    bucket_start_ns INTEGER NOT NULL,
    component TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime TEXT NOT NULL,
    model TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK(sample_count BETWEEN 0 AND 1048575),
    duration_sum_ms REAL NOT NULL,
    duration_min_ms REAL NOT NULL,
    duration_max_ms REAL NOT NULL,
    histogram_p0 INTEGER NOT NULL CHECK(histogram_p0 >= 0),
    histogram_p1 INTEGER NOT NULL CHECK(histogram_p1 >= 0),
    histogram_p2 INTEGER NOT NULL CHECK(histogram_p2 >= 0),
    histogram_p3 INTEGER NOT NULL CHECK(histogram_p3 >= 0),
    histogram_p4 INTEGER NOT NULL CHECK(histogram_p4 >= 0),
    PRIMARY KEY(bucket_start_ns, component, operation, status, runtime, model)
) WITHOUT ROWID;

CREATE TABLE daily_span_stats (
    bucket_start_ns INTEGER NOT NULL,
    component TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime TEXT NOT NULL,
    model TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    duration_sum_ms REAL NOT NULL,
    duration_min_ms REAL NOT NULL,
    duration_max_ms REAL NOT NULL,
    duration_b00 INTEGER NOT NULL,
    duration_b01 INTEGER NOT NULL,
    duration_b02 INTEGER NOT NULL,
    duration_b03 INTEGER NOT NULL,
    duration_b04 INTEGER NOT NULL,
    duration_b05 INTEGER NOT NULL,
    duration_b06 INTEGER NOT NULL,
    duration_b07 INTEGER NOT NULL,
    duration_b08 INTEGER NOT NULL,
    duration_b09 INTEGER NOT NULL,
    duration_b10 INTEGER NOT NULL,
    duration_b11 INTEGER NOT NULL,
    duration_b12 INTEGER NOT NULL,
    duration_b13 INTEGER NOT NULL,
    duration_b14 INTEGER NOT NULL,
    PRIMARY KEY(bucket_start_ns, component, operation, status, runtime, model)
) WITHOUT ROWID;

CREATE TABLE hourly_metric_stats (
    bucket_start_ns INTEGER NOT NULL,
    component TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    unit TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime TEXT NOT NULL,
    model TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    value_sum REAL NOT NULL,
    value_min REAL NOT NULL,
    value_max REAL NOT NULL,
    PRIMARY KEY(
        bucket_start_ns, component, name, kind, unit, operation, status,
        runtime, model
    )
) WITHOUT ROWID;

CREATE TABLE daily_metric_stats (
    bucket_start_ns INTEGER NOT NULL,
    component TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    unit TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime TEXT NOT NULL,
    model TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    value_sum REAL NOT NULL,
    value_min REAL NOT NULL,
    value_max REAL NOT NULL,
    PRIMARY KEY(
        bucket_start_ns, component, name, kind, unit, operation, status,
        runtime, model
    )
) WITHOUT ROWID;

CREATE TABLE telemetry_watermarks (
    stage TEXT PRIMARY KEY NOT NULL CHECK(stage IN ('raw', 'hour', 'day')),
    through_ns INTEGER NOT NULL,
    updated_at_ns INTEGER NOT NULL
) WITHOUT ROWID;
