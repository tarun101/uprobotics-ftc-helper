# UP Robotics FTC Helper

A community tool for the FIRST Tech Challenge season: a student asks a question and gets a short answer
with links to the exact source. This file is the glossary for the project; the README describes how it is built.

## Language

### What gets indexed

**Source**:
An origin document somewhere on the web that the Helper reads: a rule or section of the Competition Manual,
a Team Update, a Q&A entry, the game hub, a YouTube video, or a page of a design or documentation site.
_Avoid_: document, page (when the origin is meant)

**Source type**:
The category of a Source: manual, team_update, qa, hub, video, guide, docs, vendor_docs.
_Avoid_: kind, category

**Item**:
One indexed Markdown document derived from a Source, identified by one key. An Item is the smallest thing
that is uploaded, replaced, or deleted.
_Avoid_: unit, file, document, entry

**Window**:
The kind of Item that covers a 2–3 minute stretch of one video, with a link that opens the video at that moment.
_Avoid_: segment, clip, transcript

**Chunk**:
A fragment of an Item that retrieval returns for a question. Several Chunks may come from one Item.
_Avoid_: snippet, passage, result

### Official content

**Manual**:
The season's Competition Manual published by FIRST, the only place game rules are defined.
_Avoid_: rulebook, game manual (that is Game Manual 0, a guide)

**Rule**:
A numbered entry in the Manual, such as G202 or R503, with its own anchor in the official page.

**Section**:
A headed part of the Manual that is not itself a Rule, such as 9.6.2, holding text, tables, and figures.

**Team Update**:
A dated PDF from FIRST that changes or clarifies the Manual. The newest Team Update outranks older Manual text.
_Avoid_: update, errata, bulletin

**Q&A entry**:
One answered question in FIRST's official Q&A system. It outranks older Manual text, like a Team Update.
_Avoid_: ruling, FAQ

**Hub**:
FIRST's season page listing the current Manual version, Team Updates, and key dates.
_Avoid_: resources page, landing page

### Community and vendor content

**Guide**:
A community-written design and strategy site, such as Game Manual 0. Advice and examples, never rules.

**Docs**:
FIRST's own programming and control-system documentation (FTC Docs). Guidance, never rules.

**Vendor docs**:
A parts maker's product documentation, such as REV Robotics. Product guidance, never rules.

**Video**:
A YouTube video from one of the configured channels. Advice and examples, never rules.

### Time

**Season**:
One FIRST Tech Challenge game year, named by FIRST (for example 2026-27, BIOBUZZ). The Helper answers for
the current Season.

**Season label**:
The Season a Source belongs to, written on every Window so an answer can say when advice is from an earlier Season.

**Rollover**:
The switch to a new Season when FIRST publishes its Manual: old-season rules content (Manual, Team Updates,
Q&A entries, Hub) is archived, while Videos and Guides stay with their Season label and rank below current ones.
_Avoid_: reset, new year, migration

**Archive** (of an Item):
Removing an old-season Item from the index so it can no longer be retrieved. Archived rules are still readable at FIRST.
_Avoid_: delete, purge (in prose)

### Operation

**Run**:
One scheduled execution of the indexer on the Mac mini: check official content, fetch new Sources, upload
changed Items, delete stale ones, then ping the Monitor.
_Avoid_: job, sync, cron

**Round**:
The YouTube portion of a Run, limited to a fixed number of videos so that YouTube is never asked for too much in a day.
_Avoid_: batch, backfill (backfill is the state of catching up, not a Round)

**Monitor**:
The outside service that emails the maintainer when no Run has reported success for two days.
_Avoid_: healthcheck, dead-man's switch (in prose)

**Helper**:
The whole product as students see it: the page, the answers, and the links.
_Avoid_: bot, chatbot, assistant
