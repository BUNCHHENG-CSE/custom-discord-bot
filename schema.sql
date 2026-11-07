CREATE TABLE IF NOT EXISTS guilds (
    id BIGINT PRIMARY KEY,
    log_channel BIGINT,
    welcome_channel BIGINT,
    goodbye_channel BIGINT,
    welcome_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    goodbye_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Keep existing installations compatible when the guilds table predates the
-- welcome system.
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS welcome_channel BIGINT;
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS goodbye_channel BIGINT;
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS welcome_config JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS goodbye_config JSONB NOT NULL DEFAULT '{}'::jsonb;

-- A guild can nominate any number of Discord roles as bot staff roles.  Role
-- IDs are only unique inside the context of a guild, so the composite primary
-- key is also the required (guild_id, role_id) unique constraint.
CREATE TABLE IF NOT EXISTS guild_staff_roles (
    guild_id BIGINT NOT NULL,
    role_id BIGINT NOT NULL,
    is_staff BOOLEAN NOT NULL DEFAULT TRUE,
    role_type TEXT NOT NULL DEFAULT 'mod',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, role_id)
);

ALTER TABLE guild_staff_roles ADD COLUMN IF NOT EXISTS role_type TEXT NOT NULL DEFAULT 'mod';

CREATE INDEX IF NOT EXISTS guild_staff_roles_active_guild_idx
    ON guild_staff_roles (guild_id)
    WHERE is_staff = TRUE;

-- Roles automatically assigned when a member joins. The table supports more
-- than one role per guild even though the common setup uses one Member role.
CREATE TABLE IF NOT EXISTS guild_autoroles (
    guild_id BIGINT NOT NULL,
    role_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, role_id)
);

-- One emoji can grant one role on a particular message. The channel is stored
-- so the event can be matched without another Discord API request.
CREATE TABLE IF NOT EXISTS reaction_roles (
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    emoji TEXT NOT NULL,
    role_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, message_id, emoji)
);

CREATE INDEX IF NOT EXISTS reaction_roles_message_idx
    ON reaction_roles (guild_id, channel_id, message_id);

CREATE TABLE IF NOT EXISTS mod_cases (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, moderator_id BIGINT NOT NULL,
    action TEXT NOT NULL, reason TEXT, duration INT, expires_at TIMESTAMPTZ,
    active BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL, channel_id BIGINT UNIQUE NOT NULL, user_id BIGINT NOT NULL,
    panel TEXT, status TEXT DEFAULT 'open', claimed_by BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW(), closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS giveaways (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, message_id BIGINT UNIQUE NOT NULL,
    prize TEXT NOT NULL, winners INT DEFAULT 1, ends_at TIMESTAMPTZ NOT NULL,
    host_id BIGINT NOT NULL, requirements JSONB DEFAULT '{}'::jsonb, ended BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INT REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL, PRIMARY KEY (giveaway_id, user_id)
);

CREATE TABLE IF NOT EXISTS feeds (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, url TEXT NOT NULL,
    last_seen TEXT, last_polled_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tags (
    guild_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    author_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (guild_id, name)
);

-- Optional feature columns on guilds (non-destructive)
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS starboard_channel BIGINT;
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS starboard_threshold INT NOT NULL DEFAULT 3;
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS suggest_channel BIGINT;
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS music_247 BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE guilds ADD COLUMN IF NOT EXISTS disabled_commands JSONB NOT NULL DEFAULT '[]'::jsonb;


CREATE TABLE IF NOT EXISTS user_levels (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    xp BIGINT NOT NULL DEFAULT 0,
    level INT NOT NULL DEFAULT 0,
    messages BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id BIGINT NOT NULL,
    level INT NOT NULL,
    role_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, level)
);

CREATE TABLE IF NOT EXISTS afk_users (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'AFK',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    reminder TEXT NOT NULL,
    remind_at TIMESTAMPTZ NOT NULL,
    sent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS starboard_entries (
    guild_id BIGINT NOT NULL,
    source_message_id BIGINT NOT NULL,
    starboard_message_id BIGINT NOT NULL,
    stars INT NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, source_message_id)
);


