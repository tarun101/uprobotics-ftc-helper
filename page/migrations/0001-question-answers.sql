ALTER TABLE question_events ADD COLUMN answer TEXT;
ALTER TABLE question_events ADD COLUMN answer_updated_at TEXT;
CREATE INDEX IF NOT EXISTS question_events_public_by_time ON question_events(endpoint, occurred_at DESC);
