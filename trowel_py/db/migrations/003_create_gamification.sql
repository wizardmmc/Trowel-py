
create table if not exists players(
    id text primary key default 'default', -- trowel is a single-user system.
    level integer default 1,
    xp integer default 0,
    coins integer default 0,
    streak_days integer default 0,
    last_active text,
    created_at text default (datetime('now'))
);

create table if not exists pets(
    player_id text primary key references players(id) on delete cascade,
    mood text default 'normal' check(mood in ('happy', 'excited', 'curious', 'normal')),
    hunger integer default 80 check(hunger between 0 and 100),
    equipped_hat text,
    updated_at text default (datetime('now'))
);

create table if not exists inventory(
    id text primary key,
    player_id text not null references players(id) on delete cascade,
    item_id text not null, --e.g. apple, orange
    item_type text not null check(item_type in ('hat', 'food')),
    equipped integer default 0,
    obtained_at text default (datetime('now'))
);

create table if not exists event_log(
    id text primary key,
    player_id text not null references players(id) on delete cascade,
    event_type text not null,
    reward_xp integer default 0,
    reward_coin integer default 0,
    reward_item_id text,
    triggered_at text default (datetime('now')) -- used to sort event or calculate due time
);

create index if not exists idx_event_log_type on event_log(event_type, triggered_at); -- like "find all daily_logon type event and sort by time"

create table if not exists event_cooldowns( -- anti-brush, like 'daily login'
    event_type text primary key,
    last_triggered text
);

create table if not exists user_preferences(
    card_id text references cards(id) on delete cascade,
    liked integer default 0,
    created_at text default (datetime('now')),
    primary key (card_id) -- equivalent notion on primary key
);

create table if not exists cold_start_answers( -- used for record q-a when user first used trowel?
    id text primary key,
    question text not null,
    answer text not null,
    created_at text default (datetime('now'))
);