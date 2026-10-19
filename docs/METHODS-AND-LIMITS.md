# Methods and scope decisions

This document explains how the implementation handles the professor's capping
telemetry tasks. It separates quantities that are directly observed from
physical production and causal interpretations that the supplied telemetry
does not establish.

## Closure observations

The builder reads configured CSV files in order, validates types and
timestamps, and carries the final counter observation into the next file.
For each head, it records an event only when the counter increases by **exactly
one** from the previous observed row. Torque and status come from the new row.
The first observation establishes a baseline. Holds, larger jumps and
decreases are measured but do not generate reconstructed closure records.

Raw rows with duplicate timestamps are retained and counted as a quality
signal. Neither the builder nor the analytics silently removes duplicate
rows. Thus “observed exact +1 events” is the population that the current
system can justify. It is **not** a certified count of all unique physical
closures. If the intended requirement is to remove a particular class of
duplicate closure events, the professor and data owner need to define its
event identity and conflict resolution rule first. Timestamp alone is
insufficient because 36 heads can close during the same second.

## Status and denominators

Status `0` denotes a successful observed closure. Codes `2` and `3` decode
as No Load. A separate `cap_present` flag can be unknown for other statuses.
The integrated confirmed-cap success fraction is:

`count(status=0 and cap_present=True) / count(cap_present=True)`

The report also shows status-0 / all observed events, No Load / all observed
events, unknown cap presence, and Person A's original non-No-Load fraction.
These are different questions with different denominators. No Load should not
automatically count as a failed cap application, and a near-100% cap-present
fraction is not a measured total-production success rate. Threshold values
such as 1.5–2.5 Nm are configuration examples requiring confirmation from
the process owner.

## Machine-wide No Load intervals

`machine_idle` reads **raw status readings**, including the first raw row,
from the partitioned pool. The question must specify one machine and bounded
start/end. Every status column must decode to No Load (code `2` or `3`) on
each consecutive observed one-second row. The configured threshold
`analytics.idle_window_seconds` defaults to 300. A gap, repeated timestamp,
other status, or a missing head breaks continuity. Runs can cross CSV
boundaries when timestamps remain consecutive. Each reading represents one
observed one-second slot, and report intervals are half open.

The output calls these **candidate all-head No Load intervals**. It does not
establish operating schedule, physical downtime, or an upstream cause. A
request that would collect more raw readings than `data.max_loaded_events`
asks for a narrower time window.

## Capping speed and time buckets

`kpi_over_time` and `observed_throughput` divide observed exact +1 events by
the **requested interval duration** and scale to events/hour. They include
empty and clipped partial hour/day buckets. The overall rate uses all events
over the full requested duration. Separately, the tools call Person A's
`incremental_average` for a running, **unweighted mean of per-bucket rates**.
When buckets have unequal durations, the final running mean can differ from
the overall rate; both numbers are labelled in the report. Neither is an OEE
metric or a rate over confirmed operating time. Timestamps are used as
stored; their timezone and DST semantics have not been confirmed.

## Per-day head results

`head_success_by_day` answers questions asked per day ("each head on each day",
"the worst head each day"). It splits the selected scope into calendar days of
the recorded clock (00:00 to 24:00, UTC by contract) and aggregates **each day
with its own lazy query**, so memory stays bounded however many days the pool
holds; the events are never loaded. For each day it reports every head's
confirmed cap-present success and rejection (failure) counts and rates, the
day's all-heads rates, and four picks: lowest and highest success, lowest and
highest failure. Picks compare exact ratios (no floating-point rounding), list
**every** head that shares the picked value, and leave out heads with fewer
than `analytics.min_n` cap-present closures that day. A day the data covers for
less than 23 hours is marked partial. Each day's ranking was checked against a
one-window `rank_heads_by_success` over the same day.

Head rankings and peer comparisons (`rank_heads_by_success`,
`compare_head_success`) are also aggregated while scanning, so they work on any
window, including all the data, and never drop a peer to fit the event limit.

## Direct answers

Every result starts with a direct answer (`src/agent/answer.py`): a sentence
and the form the question asked for, which is one of: none (the sentence
alone), chart, per-head table, head x day table, per-day table, or interval
table. With the rules, the form comes from the wording ("plot", "each head",
"each day", "how many") and the sentence from a template per tool. With Claude,
a second request gives Claude the question and the computed results; Claude
writes the sentence and chooses the form from those the tools can produce.

Claude's sentence is accepted only if every number in it is found in the tool
results. Before the check, dates, times, head and machine identifiers are
removed; a number matches when a result value, or that value as a percentage,
rounds to it at the number of decimals written; 0, 1 and 100 are always
accepted as descriptors. Any unmatched number rejects Claude's answer, the
built-in sentence is shown instead, and the trace records the unmatched
numbers. Tables are always rendered from the tool results, never from model
text.

Which side of a ranking to show, and whether to lead with success or failure,
is read from the wording. Failure, error, reject or defect without "success"
means failure-first; lowest/least/fewest/best failure, or highest success,
means the best end; otherwise the worst end. This changes presentation only:
the same tools run and return the same numbers.

## Uploaded and session data

A daily CSV added to the shared data is appended as the next partition of the
pool (`append_to_event_pool`). The previous counter baseline and head set are
re-derived from the stored raw partition exactly as the builder does, so a pool
grown one file at a time equals a pool built from all the files at once (same
summary, partitions and events; tested, and checked on the real Feb 1-5 files).
Appending refuses a file already in the pool, a file for another machine and a
file that starts before the pool ends, before the store is touched.

The store (a Supabase bucket, or a folder for testing) holds the manifest and
the raw and event Parquet files. New partition files are written before the
manifest, so the store always describes a complete pool; a failed upload leaves
only unreferenced files, and the local copy is reset to the store. Files read
back are checked against the manifest's event hashes.

Session files ("My own CSV files") are built into a separate throwaway pool for
one browser session, in date order of their AROL export names; they never touch
the store. They are removed with *Remove my files*, when the host restarts, or,
once older than six hours, when the next new session starts.

## Report visuals

The CLI's `--plots` flag creates PNGs from the successful tool **results**
and links them in the saved Markdown report. A histogram uses returned bin
edges and counts; a trend uses the returned moving-average series; per-head
plots use explicit cap-present fractions; time plots use returned observed
bucket rates. Long trend series sample returned points for display, retaining
the first and last; the tool result stays complete. The optional HTML output
uses the same findings. PDF export depends on a locally available supported
browser and is not needed for the required Markdown report.

When at least 95% of 100 or more torque readings fall into one requested
full-range histogram bin, the analysis also returns a **display zoom**. It
uses the nearest observed 1st and 99th percentile values as bounds, repeats
the requested bin count for readings inside those bounds, and states how many
readings lie outside. The plot shows both the original full-range histogram
and the central view. The reported full-range counts and sample size never
change; the zoom must not be read as a replacement population or a process
specification.

## Agent decisions and evidence

The default rules planner parses supported questions, preserves explicit
scope, and asks for clarification on unsupported or ambiguous wording. The
bounded local Ollama option validates one proposed torque analysis against
the deterministic grammar before dispatch. The Claude planner (Anthropic
API, `src/agent/claude_planner.py`) maps everyday wording to any registered
tool; its proposals are validated for names, types, supported combinations,
pool heads and data range before data load. Its prose is used as a
clarification question or, after the tools ran, as the direct-answer sentence,
which is accepted only when every number in it matches the tool results (see
Direct answers); for that wording the computed results, never raw telemetry
rows, are sent to the Anthropic API. Its live checks (16/16 planning, 24/24
end-to-end on a three-day pool, and an 11-question direct-answer audit on the
Feb 1-5 pool) measure small hand-written question sets, not general language
accuracy. All numeric findings come from registered deterministic tools, not
model prose. Each run records planned
calls, data load, results and trace. Controlled tests and synthetic labels
show software behavior, while the recorded real-data comparisons demonstrate
selected scoped routes; neither proves physical fault detection or broad
natural-language accuracy.
