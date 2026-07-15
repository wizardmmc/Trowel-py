"""profile HTTP domain (slice-049): GET/PUT /api/profile.

Thin FastAPI layer over the memory store (slice-047). Domain logic
(``profile_to_body`` / ``body_to_profile`` / ``validate_profile``) lives in
``trowel_py.memory.profile``; this package only holds the HTTP adaptation
(routes / service / schemas).
"""
