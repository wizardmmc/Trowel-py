create table if not exists feynman_sessions(
    id text primary key,
    card_id text not null references cards(id) on delete cascade,
    question text not null,
    user_answer text,
    accuracy integer check(accuracy between 0 and 100),
    completeness integer check (completeness between 0 and 100),
    feedback text,
    missed_points text,
    created_at text default (datetime('now'))
);

create table if not exists follow_up_threads(
    id text primary key,
    card_id text not null references cards(id) on delete cascade,
    created_at text default (datetime('now'))
);

create table if not exists follow_up_messages(
    id text primary key,
    thread_id text not null references follow_up_threads(id) on delete cascade,
    role text not null check(role in ('user', 'assistant')),
    content text not null,
    created_at text default (datetime('now'))
);