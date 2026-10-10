"""Curated public endpoints. Unavailable sources are reported, never synthesized.

No authentication credentials, paid data APIs, untrusted arbitrary URL crawler,
or circumventing publisher access restrictions.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Source:
    key: str
    title: str
    category: str
    adapter: str
    endpoint: str
    reliability: float
    interval_minutes: int = 60

SOURCES = (
    Source("fed", "US Federal Reserve", "macro", "rss", "https://www.federalreserve.gov/feeds/press_all.xml", 1., 45),
    Source("ecb", "European Central Bank", "macro", "rss", "https://www.ecb.europa.eu/rss/press.html", 1., 60),
    Source("bls", "US Bureau of Labor Statistics", "macro", "rss", "https://www.bls.gov/feed/news_release/rss.xml", 1., 90),
    Source("sec", "US SEC press releases", "regulation", "rss", "https://www.sec.gov/news/pressreleases.rss", 1., 75),
    Source("sec_filings", "SEC filings", "stocks", "rss", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom", 1., 60),
    Source("cisa", "CISA advisories", "cybersecurity", "rss", "https://www.cisa.gov/cybersecurity-advisories/all.xml", 1., 90),
    Source("coinbase_status", "Coinbase system status", "crypto", "rss", "https://status.coinbase.com/history.rss", .98, 45),
    Source("bbc_business", "BBC Business", "markets", "rss", "https://feeds.bbci.co.uk/news/business/rss.xml", .83, 60),
    Source("bbc_world", "BBC World", "geopolitics", "rss", "https://feeds.bbci.co.uk/news/world/rss.xml", .83, 60),
    Source("un_news", "United Nations News", "geopolitics", "rss", "https://news.un.org/feed/subscribe/en/news/all/rss.xml", .98, 120),
    Source("eia", "US Energy Information Administration", "commodities", "rss", "https://www.eia.gov/rss/todayinenergy.xml", .98, 180),
    Source("coindesk", "CoinDesk", "crypto", "rss", "https://www.coindesk.com/arc/outboundfeeds/rss/", .80, 60),
    Source("cointelegraph", "Cointelegraph", "crypto", "rss", "https://cointelegraph.com/rss", .71, 75),
    Source("gdelt_macro", "GDELT global macro", "macro", "gdelt", "(central bank OR inflation OR sanctions OR tariffs)", .65, 75),
    Source("gdelt_geo", "GDELT geopolitical coverage", "geopolitics", "gdelt", "(oil OR shipping OR war OR ceasefire) AND (disruption OR sanctions OR attack)", .65, 90),
    Source("github_bitcoin", "Bitcoin Core releases", "technology", "github", "bitcoin/bitcoin", 1., 240),
    Source("github_ethereum", "Ethereum client releases", "technology", "github", "ethereum/go-ethereum", 1., 240),
    Source("github_solidity", "Solidity releases", "technology", "github", "ethereum/solidity", 1., 240),
    Source("github_openai", "OpenAI Python releases", "technology", "github", "openai/openai-python", 1., 360),
    Source("btc_network", "Bitcoin network fees", "onchain", "mempool", "https://mempool.space/api/v1/fees/recommended", .98, 60),
)
SOURCE_BY_KEY = {s.key: s for s in SOURCES}
