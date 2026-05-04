# Run P-300 paper trading with a clean, filtered console stream.
#
# Edit $noise below to add/remove patterns. Each line is a regex matched
# (case-sensitive by default) against each log line; matching lines are
# hidden. To bring something back temporarily, comment its line out.
#
# Usage:
#   .\tools\p300_run.ps1            # run with --feed and noise filter
#   .\tools\p300_run.ps1 -Verbose   # show everything (no filter)
#   .\tools\p300_run.ps1 -NoFeed    # skip --feed (assume separate binance_feed)
#
# Run from the repo root so `python run.py` resolves correctly.

param(
    [switch]$Verbose,
    [switch]$NoFeed
)

# Patterns to hide from the console stream. Edit freely.
# Strings inside `'...'` are literal regex (escape `[` `]` `(` `)` `.` etc).
# Strings inside `"..."` allow single quotes without escaping.
$noise = @(
    'already_recorded',     # JPLUS-CORE idempotent re-checks (1439/day in steady state)
    'no_signal',            # tactical sleeve: no entry condition met
    'not_thursday',         # S-096 V4: only fires on Thursdays
    "'no_gap'",             # PDO: no gap-up condition today
    "'open_waiting'",       # sleeve has an open trade, waiting for exit
    '\[feed\]',             # binance_feed thread per-tick refresh summary
    'tick ok',              # variant_engine 60s heartbeat
    "S-096.*'status': 'ok', 'actions': \[\]", # S-096 idle (no entry/exit)
    "no_upcoming_fomc"      # FOMC dispatcher: no FOMC within 2-day lookahead
)

$args_list = @()
if (-not $NoFeed) { $args_list += '--feed' }

if ($Verbose) {
    python run.py @args_list 2>&1
} else {
    $pattern = $noise -join '|'
    python run.py @args_list 2>&1 | Where-Object { $_ -notmatch $pattern }
}
