# Overlapping z2/z3 sizing study

Frozen before this study's outcomes are computed. Existing history has already been used in earlier research; no untouched holdout is claimed. Research only, with no order client or production changes.

Question: do simultaneous original/strong-cushion signals justify larger positions, and what happens if each existing lane may enter the same market once?

Fixed comparisons: current five-contract single-owner baseline; seven or ten contracts when BOTH signal gates qualify at the entry decision, falling back to five if depth or current risk limits prevent the larger order; corresponding strict controls requiring the larger execution also satisfy BOTH lanes' price limits/floors; and independent five-contract entries per lane, maximum ten per market, same side only while the first position is open. No threshold optimization.

Simultaneous intersection is ask 93–<95c, z>=3, 3–10 minutes, spread<=2c. Both lanes calculate the same z, so overlap is not independent confirmation. A later signal cannot increase the earlier initial order. Separate later add-ons from simultaneous double entries.

Reuse the archived raw-input reconstruction of 19,320 markets, August 13–October 2 UTC. Compare early chronological periods, September 12–October 1, October 2, and the continuous 51-day run. Next-minute opening quotes are assumed fills without depth proof. Earlier recorded books use the same 25-second bounded rechecks and a ten-second minimum-delay control. Recompute seven/ten-contract book depth and fees at the actual baseline five-contract opportunity timestamp. Do not wait longer for the larger size; fall back to five. Two orders at the same book observation consume disjoint displayed quantity, not the same depth twice.

Keep shared max20 contracts/$19 reserved exposure/$10 daily loss including open risk, minimum cash $5, initial modeled cash $50, exact settlement release, and z3 own limits unchanged. One asset position except explicitly allowing a second same-market same-side leg in the independent-lanes experiment. Fees plus 1c/contract primary, 2c and full limit-price costs as sensitivities. Size increases change portfolio selection and must not be valued by multiplying five-contract profit.

Validate baseline market/price/P&L parity against the existing simulator for every period and execution scenario. Test causal overlap, insufficient larger-size depth, combined-depth consumption, maximum one leg per lane, opposite-side rejection, settlement ordering, and all portfolio limits. Publish failed comparisons as well as positive ones. Preserve stable checkpoint and activated production sources.
