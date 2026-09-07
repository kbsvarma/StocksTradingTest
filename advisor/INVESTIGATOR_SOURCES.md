# Investigator source coverage catalog

43 source routes; 18 analytical dimensions. Actual availability is recorded per run.

| Source | Dimension | Collection route | Clock | Interpretation trap |
|---|---|---|---|---|
| [SEC XBRL company facts](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | fundamentals | automatic | acceptance + fiscal start/end | Restatements and YTD durations cannot replace the latest quarter |
| [SEC submissions and filing text](https://data.sec.gov/) | accounting | automatic | acceptance + report date | Filing existence alone is not a directional finding |
| [8-K earnings releases and exhibits](https://www.sec.gov/edgar/search/) | catalysts | automatic | acceptance + actual event date | Release date and event date can differ |
| [Issuer investor relations and results](https://www.sec.gov/edgar/search/) | guidance | automatic_and_agent_web | publication + guidance target period | Management incentives; reconcile GAAP and adjusted numbers |
| [Earnings prepared remarks and Q&A](https://site.financialmodelingprep.com/developer/docs) | guidance | agent_web | call date + fiscal quarter | Compare Q&A and prior guidance; do not infer sentiment from one adjective |
| [Form 4 / 144 insider transactions](https://www.sec.gov/edgar/search/) | ownership | automatic | transaction + filing | Grants, tax withholding and planned sales are not discretionary conviction |
| [13D / 13G / 13F ownership](https://www.sec.gov/edgar/search/) | ownership | automatic | position date + filing | 13F lag and omitted shorts prevent net-exposure inference |
| [S-3 / 424B / prospectus / convertibles](https://www.sec.gov/edgar/search/) | financing | automatic | filing + effective/transaction date | Shelf authorization is not completed issuance |
| [Auditor, non-GAAP, accrual and footnote review](https://www.sec.gov/edgar/search/) | accounting | agent_web | fiscal period + filing | Sector-specific accounting and acquisition effects |
| [Yahoo adjusted daily bars](https://finance.yahoo.com/) | technicals | automatic | exchange session + retrieval | EOD is not executable live price |
| [Yahoo profile and quote snapshot](https://finance.yahoo.com/) | valuation | automatic | quote time; other fields observation-only | Do not make undated ratios or growth current facts |
| [Yahoo EPS/revenue trends and revisions](https://finance.yahoo.com/) | expectations | automatic | retrieval + target fiscal period | Current consensus cannot reconstruct pre-event expectations |
| [FMP period-specific analyst estimates](https://site.financialmodelingprep.com/developer/docs/stable/analyst-estimates) | expectations | credential_adapter | snapshot + estimate target period | Consensus definition/sample and adjustment basis must match |
| [Finnhub timestamped company news](https://finnhub.io/docs/api/company-news) | news | credential_adapter | publication + underlying event | Syndicated copies are not independent evidence |
| [Yahoo company news discovery](https://finance.yahoo.com/) | news | automatic | publication + retrieval | Headlines are leads; source body required for substantial claims |
| [Reuters / AP / other reported news](https://www.reuters.com/) | news | agent_web | original publication + update + event | An updated page can recirculate an old event |
| [Business Wire / PR Newswire / issuer releases](https://www.businesswire.com/) | catalysts | agent_web | publication + effective date | Promotional claims need independent corroboration |
| [FINRA / exchange short interest](https://www.finra.org/finra-data/browse-catalog/equity-short-interest) | positioning | agent_web | settlement + publication | Short-sale volume is NOT outstanding short interest |
| [Broker or securities-lending borrow rate/utilization](https://www.interactivebrokers.com/en/trading/short-securities-availability.php) | positioning | import | quote/observation time | Availability is broker-specific; high fees are not guaranteed squeezes |
| [FINRA short-sale volume](https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data) | microstructure | agent_web | trade session + publication | Cannot derive short interest or directional net positions |
| [Yahoo options chain snapshot](https://finance.yahoo.com/) | options | automatic | quote/last trade + expiry + OI session | Volume/OI does not reveal opening direction or dealer inventory |
| [Cboe licensed options quotes and sentiment](https://datashop.cboe.com/option-quote-intervals) | options | import | NBBO/transaction + expiry | No automated extraction of Cboe delayed-quote website |
| [OPRA options trades and classified flow](https://www.opraplan.com/) | options | import | trade + quote + classification | Trade-side inference is uncertain; multi-leg trades must be grouped |
| [Analyst recommendations and target dispersion](https://finance.yahoo.com/) | sentiment | automatic | rating date + estimate period | Target-price average is neither fair value nor expected return |
| [Stocktwits / Reddit / other public discussion](https://stocktwits.com/) | sentiment | agent_web | post + capture + original event | Bots, selection bias, duplicates and ticker ambiguity |
| [Google Trends attention](https://trends.google.com/) | sentiment | agent_web | sample window + retrieval | Relative sampled attention does not identify buying |
| [Competitor filings, pricing and product releases](https://www.sec.gov/edgar/search/) | industry | adaptive | publication + relevant operating period | Peer beats do not automatically benefit the issuer |
| [Customer capex, budgets and purchase commitments](https://www.sec.gov/edgar/search/) | industry | adaptive | fiscal period + announcement + delivery | Announcements are not firm backlog or recognized revenue |
| [Supplier capacity, lead times and inventory](https://www.sec.gov/edgar/search/) | industry | adaptive | publication + operating period | Capacity allocation and double ordering can distort demand |
| [Sector shipment, utilization and demand data](https://www.semiconductors.org/) | industry | agent_web | measurement window + release | Aggregation and sample composition matter |
| [FRED rates, credit spreads and financial conditions](https://fred.stlouisfed.org/) | macro | automatic | observation + retrieval; vintage for history | Revised macro series are not historical point-in-time releases |
| [ALFRED macro vintages](https://fred.stlouisfed.org/docs/api/fred/alfred.html) | macro | agent_web | realtime vintage + observation | Choose vintage before the decision, not the latest revision |
| [BLS / BEA inflation, employment and demand](https://www.bls.gov/) | macro | agent_web | release + reference month/quarter | Distinguish revisions and seasonal adjustments |
| [Federal Reserve decisions and minutes](https://www.federalreserve.gov/newsevents.htm) | macro | agent_web | release + meeting date | Minutes describe an earlier meeting |
| [BIS / Federal Register export and trade rules](https://www.bis.gov/) | regulatory | agent_web | publication + effective date | Proposal, guidance, final rule and license are different |
| [FTC / DOJ / courts / merger documents](https://www.ftc.gov/news-events) | regulatory | agent_web | filing + hearing + effective date | Allegation is not a judgment; deal terms and funding matter |
| [FDA approvals, labels and advisory calendars](https://www.fda.gov/) | regulatory | agent_web | decision + announcement + effective date | Approval probability cannot be read from promotional trial news |
| [ClinicalTrials.gov trial records and results](https://clinicaltrials.gov/data-api/about-api) | catalysts | agent_web | last posted + study/event dates | Sponsor-reported timelines can move; endpoints and populations matter |
| [EIA inventories, supply and energy demand](https://www.eia.gov/opendata/) | industry | agent_web | reference week + release | Weather, seasonality and revisions affect interpretation |
| [USASpending / agency contract awards](https://api.usaspending.gov/) | industry | agent_web | award + obligation + publication | Contract ceiling is not funded revenue |
| [TRACE / bond spreads and debt maturity disclosures](https://www.finra.org/finra-data/browse-catalog/about-corporate-and-agency-bonds) | financing | agent_web | trade + filing + maturity date | Illiquid bond prints and seniority differ |
| [Issuer ETF holdings, index changes and flows](https://www.ishares.com/) | flows | agent_web | holdings/flow date + effective rebalance | ETF volume is not net fund flow |
| [Jobs, app usage, traffic, cards, shipping and patents](https://www.uspto.gov/) | industry | import | measurement + release + revisions | Representativeness, lawful access and causal relevance are required |
