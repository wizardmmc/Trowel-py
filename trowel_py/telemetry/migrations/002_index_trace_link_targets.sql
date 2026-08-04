-- 调用详情会从目标 trace 反查来源 span；避免在每一层关联上全表扫描。
CREATE INDEX span_links_target_trace_idx
    ON span_links(linked_trace_id, linked_span_id);
