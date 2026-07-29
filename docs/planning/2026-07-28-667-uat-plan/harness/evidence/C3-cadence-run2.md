# C3 — cadence, measured on the STATE FILE not on silence (UAT run 2, 2026-07-28)

Token count is deliberately BELOW every tier (10,001 of 200,000 = 5%), so nothing can ever
emit — that removes the confound in the first attempt, where turns 2-5 were silent because the
tier had already fired rather than because the cadence throttled them. What is observed instead
is last_check_turn: it only advances on a turn where a CHECK actually ran.

turn 1: last_check_turn=1  (a check ran this turn: True)
turn 2: last_check_turn=1  (a check ran this turn: False)
turn 3: last_check_turn=1  (a check ran this turn: False)
turn 4: last_check_turn=1  (a check ran this turn: False)
turn 5: last_check_turn=1  (a check ran this turn: False)
turn 6: last_check_turn=6  (a check ran this turn: True)
turn 7: last_check_turn=6  (a check ran this turn: False)

=> checks ran on turns 1 and 6 only: the 5-turn arm. Turns 2-5 and 7 were throttled.

## The 5-MINUTE arm fires independently of turns
last_check_turn before=6 after=7; a PostToolUse(Read) with 301s elapsed ran a check
=> the minute arm fires on a NON-Bash tool, which is what the wildcard matcher is for
