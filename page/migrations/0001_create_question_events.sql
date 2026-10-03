CREATE TABLE question_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  question TEXT NOT NULL,
  country TEXT,
  city TEXT
);

CREATE INDEX idx_question_events_occurred_at ON question_events (occurred_at);
CREATE INDEX idx_question_events_country_city ON question_events (country, city);
