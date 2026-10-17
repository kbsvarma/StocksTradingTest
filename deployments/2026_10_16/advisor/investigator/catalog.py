"""Source routes and analytical coverage. A route is not a claim of live coverage."""
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class Source:
    id: str
    name: str
    dimension: str
    authority: str
    route: str
    url: str
    clock: str
    trap: str
    priority: int = 2

# Prioritize original evidence for the specific question, not one universal vendor.
SOURCES = [
 Source('sec_facts','SEC XBRL company facts','fundamentals','primary','automatic','https://www.sec.gov/search-filings/edgar-application-programming-interfaces','acceptance + fiscal start/end','Restatements and YTD durations cannot replace the latest quarter',1),
 Source('sec_filings','SEC submissions and filing text','accounting','primary','automatic','https://data.sec.gov/','acceptance + report date','Filing existence alone is not a directional finding',1),
 Source('sec_exhibits','8-K earnings releases and exhibits','catalysts','primary','automatic','https://www.sec.gov/edgar/search/','acceptance + actual event date','Release date and event date can differ',1),
 Source('issuer_ir','Issuer investor relations and results','guidance','primary','automatic_and_agent_web','https://www.sec.gov/edgar/search/','publication + guidance target period','Management incentives; reconcile GAAP and adjusted numbers',1),
 Source('transcripts','Earnings prepared remarks and Q&A','guidance','primary_or_transcript','agent_web','https://site.financialmodelingprep.com/developer/docs','call date + fiscal quarter','Compare Q&A and prior guidance; do not infer sentiment from one adjective',1),
 Source('sec_insiders','Form 4 / 144 insider transactions','ownership','primary','automatic','https://www.sec.gov/edgar/search/','transaction + filing','Grants, tax withholding and planned sales are not discretionary conviction'),
 Source('sec_ownership','13D / 13G / 13F ownership','ownership','primary','automatic','https://www.sec.gov/edgar/search/','position date + filing','13F lag and omitted shorts prevent net-exposure inference'),
 Source('sec_dilution','S-3 / 424B / prospectus / convertibles','financing','primary','automatic','https://www.sec.gov/edgar/search/','filing + effective/transaction date','Shelf authorization is not completed issuance'),
 Source('sec_quality','Auditor, non-GAAP, accrual and footnote review','accounting','primary','agent_web','https://www.sec.gov/edgar/search/','fiscal period + filing','Sector-specific accounting and acquisition effects'),
 Source('yahoo_price','Yahoo adjusted daily bars','technicals','secondary','automatic','https://finance.yahoo.com/','exchange session + retrieval','EOD is not executable live price',1),
 Source('yahoo_profile','Yahoo profile and quote snapshot','valuation','secondary','automatic','https://finance.yahoo.com/','quote time; other fields observation-only','Do not make undated ratios or growth current facts',1),
 Source('yahoo_estimates','Yahoo EPS/revenue trends and revisions','expectations','secondary','automatic','https://finance.yahoo.com/','retrieval + target fiscal period','Current consensus cannot reconstruct pre-event expectations',1),
 Source('fmp_estimates','FMP period-specific analyst estimates','expectations','secondary','credential_adapter','https://site.financialmodelingprep.com/developer/docs/stable/analyst-estimates','snapshot + estimate target period','Consensus definition/sample and adjustment basis must match'),
 Source('finnhub_news','Finnhub timestamped company news','news','secondary','credential_adapter','https://finnhub.io/docs/api/company-news','publication + underlying event','Syndicated copies are not independent evidence',1),
 Source('yahoo_news','Yahoo company news discovery','news','secondary','automatic','https://finance.yahoo.com/','publication + retrieval','Headlines are leads; source body required for substantial claims',1),
 Source('news_wires','Reuters / AP / other reported news','news','reported','agent_web','https://www.reuters.com/','original publication + update + event','An updated page can recirculate an old event'),
 Source('press_wires','Business Wire / PR Newswire / issuer releases','catalysts','issuer_statement','agent_web','https://www.businesswire.com/','publication + effective date','Promotional claims need independent corroboration'),
 Source('exchange_short','FINRA / exchange short interest','positioning','primary','agent_web','https://www.finra.org/finra-data/browse-catalog/equity-short-interest','settlement + publication','Short-sale volume is NOT outstanding short interest',1),
 Source('borrow','Broker or securities-lending borrow rate/utilization','positioning','market','import','https://www.interactivebrokers.com/en/trading/short-securities-availability.php','quote/observation time','Availability is broker-specific; high fees are not guaranteed squeezes'),
 Source('finra_volume','FINRA short-sale volume','microstructure','primary','agent_web','https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data','trade session + publication','Cannot derive short interest or directional net positions'),
 Source('options','Yahoo options chain snapshot','options','secondary','automatic','https://finance.yahoo.com/','quote/last trade + expiry + OI session','Volume/OI does not reveal opening direction or dealer inventory'),
 Source('cboe_options','Cboe licensed options quotes and sentiment','options','market','import','https://datashop.cboe.com/option-quote-intervals','NBBO/transaction + expiry','No automated extraction of Cboe delayed-quote website'),
 Source('options_flow','OPRA options trades and classified flow','options','market','import','https://www.opraplan.com/','trade + quote + classification','Trade-side inference is uncertain; multi-leg trades must be grouped'),
 Source('analyst_ratings','Analyst recommendations and target dispersion','sentiment','secondary','automatic','https://finance.yahoo.com/','rating date + estimate period','Target-price average is neither fair value nor expected return'),
 Source('social','Stocktwits / Reddit / other public discussion','sentiment','social','agent_web','https://stocktwits.com/','post + capture + original event','Bots, selection bias, duplicates and ticker ambiguity'),
 Source('search_interest','Google Trends attention','sentiment','attention','agent_web','https://trends.google.com/','sample window + retrieval','Relative sampled attention does not identify buying'),
 Source('peers','Competitor filings, pricing and product releases','industry','primary','adaptive','https://www.sec.gov/edgar/search/','publication + relevant operating period','Peer beats do not automatically benefit the issuer',1),
 Source('customers','Customer capex, budgets and purchase commitments','industry','primary','adaptive','https://www.sec.gov/edgar/search/','fiscal period + announcement + delivery','Announcements are not firm backlog or recognized revenue',1),
 Source('suppliers','Supplier capacity, lead times and inventory','industry','primary','adaptive','https://www.sec.gov/edgar/search/','publication + operating period','Capacity allocation and double ordering can distort demand'),
 Source('industry_associations','Sector shipment, utilization and demand data','industry','industry','agent_web','https://www.semiconductors.org/','measurement window + release','Aggregation and sample composition matter'),
 Source('fred','FRED rates, credit spreads and financial conditions','macro','primary','automatic','https://fred.stlouisfed.org/','observation + retrieval; vintage for history','Revised macro series are not historical point-in-time releases'),
 Source('alfred','ALFRED macro vintages','macro','primary','agent_web','https://fred.stlouisfed.org/docs/api/fred/alfred.html','realtime vintage + observation','Choose vintage before the decision, not the latest revision'),
 Source('bls_bea','BLS / BEA inflation, employment and demand','macro','primary','agent_web','https://www.bls.gov/','release + reference month/quarter','Distinguish revisions and seasonal adjustments'),
 Source('fed','Federal Reserve decisions and minutes','macro','primary','agent_web','https://www.federalreserve.gov/newsevents.htm','release + meeting date','Minutes describe an earlier meeting'),
 Source('bis_trade','BIS / Federal Register export and trade rules','regulatory','primary','agent_web','https://www.bis.gov/','publication + effective date','Proposal, guidance, final rule and license are different',1),
 Source('competition','FTC / DOJ / courts / merger documents','regulatory','primary','agent_web','https://www.ftc.gov/news-events','filing + hearing + effective date','Allegation is not a judgment; deal terms and funding matter'),
 Source('fda','FDA approvals, labels and advisory calendars','regulatory','primary','agent_web','https://www.fda.gov/','decision + announcement + effective date','Approval probability cannot be read from promotional trial news'),
 Source('clinical_trials','ClinicalTrials.gov trial records and results','catalysts','primary_registry','agent_web','https://clinicaltrials.gov/data-api/about-api','last posted + study/event dates','Sponsor-reported timelines can move; endpoints and populations matter'),
 Source('eia','EIA inventories, supply and energy demand','industry','primary','agent_web','https://www.eia.gov/opendata/','reference week + release','Weather, seasonality and revisions affect interpretation'),
 Source('usaspending','USASpending / agency contract awards','industry','primary','agent_web','https://api.usaspending.gov/','award + obligation + publication','Contract ceiling is not funded revenue'),
 Source('credit','TRACE / bond spreads and debt maturity disclosures','financing','market_primary','agent_web','https://www.finra.org/finra-data/browse-catalog/about-corporate-and-agency-bonds','trade + filing + maturity date','Illiquid bond prints and seniority differ'),
 Source('etf_flows','Issuer ETF holdings, index changes and flows','flows','primary','agent_web','https://www.ishares.com/','holdings/flow date + effective rebalance','ETF volume is not net fund flow'),
 Source('alternative','Jobs, app usage, traffic, cards, shipping and patents','industry','alternative','import','https://www.uspto.gov/','measurement + release + revisions','Representativeness, lawful access and causal relevance are required'),
]
DIMENSIONS = {
 'fundamentals':'Growth quality, margins, cash generation and balance sheet',
 'accounting':'Accruals, cash conversion, SBC, dilution, audit and non-GAAP reconciliation',
 'valuation':'Reverse expectations, multiples, cash yield and scenario sensitivity',
 'expectations':'Consensus revision, dispersion, beat quality and priced-in growth',
 'guidance':'Management change in outlook, Q&A and credibility',
 'technicals':'20/50/200-day trend, RSI, volume, volatility, drawdown and relative strength',
 'positioning':'Short interest, days to cover, borrow and squeeze conditions',
 'options':'IV/RV, skew, term structure, implied move and speculative concentration',
 'ownership':'Insider intent, activist stakes and institutional changes',
 'news':'Material new facts, source independence, novelty and contradictions',
 'sentiment':'Narrative excess, pessimism/optimism and fundamental divergence',
 'catalysts':'Dated earnings, products, regulation, contracts and binary events',
 'industry':'Customers, suppliers, competitors, demand and second-order effects',
 'regulatory':'Policy exposure, litigation, approvals and actual effective dates',
 'macro':'Rates, credit, liquidity and regime sensitivity',
 'financing':'Debt, refinancing, liquidity runway and dilution',
 'microstructure':'Liquidity, spreads, gaps and trading constraints',
 'flows':'Index, ETF and forced rebalancing demand',
}
QUESTIONS = {
 'quality_compounder':('fundamentals','Are growth, margins and cash conversion improving together? What growth is already required by valuation?'),
 'expectations_reset':('expectations','Did estimates change for the correct forward period, and has price moved more or less than the earnings change?'),
 'oversold_recovery':('technicals','Is weakness temporary in a sound business, or an early signal of fundamental deterioration? What confirms stabilization?'),
 'trend_continuation':('technicals','Does relative strength confirm improving evidence, or is this extended momentum near a catalyst?'),
 'short_squeeze':('positioning','Are high dated short interest, scarce borrow, a positive catalyst and price confirmation ALL present?'),
 'crowded_long':('sentiment','Is optimism inconsistent with revisions, valuation and price response? What forces an unwind?'),
 'neglected_turnaround':('sentiment','Does pessimism persist despite newly improving operating evidence? What falsifies the turnaround?'),
 'lottery_speculation':('options','Is the apparent upside driven by a binary event, OTM speculation and volatility rather than durable cash flows?'),
 'accounting_deterioration':('accounting','Are receivables/inventory outgrowing sales, cash lagging profit or dilution hiding per-share deterioration?'),
 'financing_stress':('financing','Do cash burn, debt maturities or issuance make the equity payoff structurally fragile?'),
 'second_order':('industry','Do customer budgets and supplier capacity confirm the issuer narrative, and who captures the economics?'),
 'catalyst_repricing':('catalysts','What dated event can change expected cash flows, by how much, versus what is priced in?'),
}

def inventory(): return [asdict(s) for s in SOURCES]
