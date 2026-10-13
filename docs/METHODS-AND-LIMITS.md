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
pool heads and data range before data load, and its prose is used only as a
clarification question. Its live checks (16/16 planning, 24/24 end-to-end on
a three-day pool) measure a small hand-written question set, not general
language accuracy. All numeric findings come from
registered deterministic tools, not model prose. Each run records planned
calls, data load, results and trace. Controlled tests and synthetic labels
show software behavior, while the recorded real-data comparisons demonstrate
selected scoped routes; neither proves physical fault detection or broad
natural-language accuracy.
