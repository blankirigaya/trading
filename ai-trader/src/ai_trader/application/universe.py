"""Curated liquid watchlist plus live-price universe selection.

The AI reviews a fixed-size board (100 symbols) but which 100 it sees depends on
the operator's mode and on real FYERS prices:

* ``cheap``     - symbols trading below the cheap-price cap.
* ``expensive`` - symbols trading at or above the cap.
* ``mixed``     - an even split of both.

``MASTER_UNIVERSE`` is deliberately broader than the reviewed board and is
ordered roughly by liquidity, so the first N names in a bucket are the most
liquid ones. Selection is deterministic for a given price map, which keeps a
symbol on the same reviewer across cycles (and restarts).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from ai_trader.config import Settings

CHEAP_MAX_PRICE_INR = Decimal("200")
DEFAULT_UNIVERSE_MODE = "cheap"
DEFAULT_UNIVERSE_SIZE = 100
UNIVERSE_MODES = ("cheap", "mixed", "expensive")

# Broad, liquid NSE cash-market list. Every entry must be a FYERS wire symbol so
# ``build_feed`` can resolve its HSM topic. Names are ordered by liquidity
# within broad groups; the price bucket decides which ones the AI actually sees.
MASTER_UNIVERSE: tuple[str, ...] = (
    # Index heavyweights and large caps.
    "NSE:RELIANCE-EQ",
    "NSE:TCS-EQ",
    "NSE:HDFCBANK-EQ",
    "NSE:ICICIBANK-EQ",
    "NSE:INFY-EQ",
    "NSE:BHARTIARTL-EQ",
    "NSE:ITC-EQ",
    "NSE:SBIN-EQ",
    "NSE:LT-EQ",
    "NSE:KOTAKBANK-EQ",
    "NSE:AXISBANK-EQ",
    "NSE:BAJFINANCE-EQ",
    "NSE:MARUTI-EQ",
    "NSE:M&M-EQ",
    "NSE:SUNPHARMA-EQ",
    "NSE:TITAN-EQ",
    "NSE:ULTRACEMCO-EQ",
    "NSE:ASIANPAINT-EQ",
    "NSE:HINDUNILVR-EQ",
    "NSE:NESTLEIND-EQ",
    "NSE:BRITANNIA-EQ",
    "NSE:DRREDDY-EQ",
    "NSE:CIPLA-EQ",
    "NSE:DIVISLAB-EQ",
    "NSE:APOLLOHOSP-EQ",
    "NSE:EICHERMOT-EQ",
    "NSE:HEROMOTOCO-EQ",
    "NSE:BAJAJ-AUTO-EQ",
    "NSE:TRENT-EQ",
    "NSE:DMART-EQ",
    "NSE:ADANIENT-EQ",
    "NSE:ADANIPORTS-EQ",
    "NSE:ADANIGREEN-EQ",
    "NSE:ADANIPOWER-EQ",
    "NSE:JSWSTEEL-EQ",
    "NSE:HINDALCO-EQ",
    "NSE:VEDL-EQ",
    "NSE:GRASIM-EQ",
    "NSE:SHREECEM-EQ",
    "NSE:AMBUJACEM-EQ",
    "NSE:ACC-EQ",
    "NSE:POWERGRID-EQ",
    "NSE:NTPC-EQ",
    "NSE:ONGC-EQ",
    "NSE:COALINDIA-EQ",
    "NSE:BPCL-EQ",
    # Technology and new-age.
    "NSE:HCLTECH-EQ",
    "NSE:TECHM-EQ",
    "NSE:WIPRO-EQ",
    "NSE:SAKSOFT-EQ",
    "NSE:PERSISTENT-EQ",
    "NSE:COFORGE-EQ",
    "NSE:MPHASIS-EQ",
    "NSE:OFSS-EQ",
    "NSE:NAUKRI-EQ",
    "NSE:IXIGO-EQ",
    "NSE:PAYTM-EQ",
    "NSE:POLICYBZR-EQ",
    "NSE:NYKAA-EQ",
    "NSE:DELHIVERY-EQ",
    "NSE:IRCTC-EQ",
    "NSE:INDIGO-EQ",
    # Financials.
    "NSE:HDFCLIFE-EQ",
    "NSE:SBILIFE-EQ",
    "NSE:JMFINANCIL-EQ",
    "NSE:ICICIGI-EQ",
    "NSE:BAJAJFINSV-EQ",
    "NSE:CHOLAFIN-EQ",
    "NSE:SHRIRAMFIN-EQ",
    "NSE:MUTHOOTFIN-EQ",
    "NSE:LICHSGFIN-EQ",
    "NSE:PNBHOUSING-EQ",
    "NSE:INDUSINDBK-EQ",
    "NSE:AUBANK-EQ",
    "NSE:BANKBARODA-EQ",
    "NSE:INDIANB-EQ",
    "NSE:RECLTD-EQ",
    "NSE:PFC-EQ",
    "NSE:CANFINHOME-EQ",
    "NSE:SBICARD-EQ",
    "NSE:PNBGILTS-EQ",
    # Autos and industrials.
    "NSE:MUNJALSHOW-EQ",
    "NSE:TVSMOTOR-EQ",
    "NSE:ESCORTS-EQ",
    "NSE:BOSCHLTD-EQ",
    "NSE:SIEMENS-EQ",
    "NSE:ABB-EQ",
    "NSE:CUMMINSIND-EQ",
    "NSE:THERMAX-EQ",
    "NSE:HAVELLS-EQ",
    "NSE:POLYCAB-EQ",
    "NSE:KEI-EQ",
    "NSE:DIXON-EQ",
    "NSE:VOLTAS-EQ",
    "NSE:BLUESTARCO-EQ",
    "NSE:CROMPTON-EQ",
    "NSE:WHIRLPOOL-EQ",
    # Consumer.
    "NSE:PIDILITIND-EQ",
    "NSE:BERGEPAINT-EQ",
    "NSE:DABUR-EQ",
    "NSE:MARICO-EQ",
    "NSE:GODREJCP-EQ",
    "NSE:COLPAL-EQ",
    "NSE:EMAMILTD-EQ",
    "NSE:VBL-EQ",
    "NSE:UBL-EQ",
    "NSE:RADICO-EQ",
    "NSE:UNITDSPR-EQ",
    "NSE:JUBLFOOD-EQ",
    "NSE:DEVYANI-EQ",
    "NSE:SAPPHIRE-EQ",
    "NSE:PAGEIND-EQ",
    "NSE:BATAINDIA-EQ",
    "NSE:ABFRL-EQ",
    # Pharma and chemicals.
    "NSE:JUBLPHARMA-EQ",
    "NSE:GRANULES-EQ",
    "NSE:LAURUSLABS-EQ",
    "NSE:NATCOPHARM-EQ",
    "NSE:AJANTPHARM-EQ",
    "NSE:GLENMARK-EQ",
    "NSE:AUROPHARMA-EQ",
    "NSE:LUPIN-EQ",
    "NSE:ALKEM-EQ",
    "NSE:TORNTPHARM-EQ",
    "NSE:IPCALAB-EQ",
    "NSE:SYNGENE-EQ",
    "NSE:MAXHEALTH-EQ",
    "NSE:FORTIS-EQ",
    "NSE:SHALBY-EQ",
    "NSE:KIMS-EQ",
    "NSE:RAINBOW-EQ",
    "NSE:VINATIORGA-EQ",
    "NSE:ATUL-EQ",
    "NSE:SRF-EQ",
    "NSE:PIIND-EQ",
    "NSE:DEEPAKNTR-EQ",
    "NSE:TATACHEM-EQ",
    "NSE:UPL-EQ",
    "NSE:BAYERCROP-EQ",
    "NSE:COROMANDEL-EQ",
    "NSE:GNFC-EQ",
    "NSE:CHAMBLFERT-EQ",
    "NSE:AARTIIND-EQ",
    "NSE:NAVINFLUOR-EQ",
    "NSE:FINEORG-EQ",
    "NSE:ALKYLAMINE-EQ",
    # Sub-200 candidates (state banks, PSUs, small/mid caps).
    # Popular sub-200 additions, first in the bucket so cheap mode reviews them.
    # Banks, finance, and PSU traders.
    "NSE:BANKINDIA-EQ",
    "NSE:PSB-EQ",
    "NSE:IFCI-EQ",
    "NSE:MMTC-EQ",
    "NSE:PAISALO-EQ",
    "NSE:DHANBANK-EQ",
    "NSE:SBFC-EQ",
    "NSE:SATIN-EQ",
    # Power, energy, and engineering PSUs.
    "NSE:PTC-EQ",
    "NSE:MRPL-EQ",
    "NSE:VASCONEQ-EQ",
    "NSE:PRAKASH-EQ",
    # Media, cable, and education.
    "NSE:GTPL-EQ",
    "NSE:HATHWAY-EQ",
    "NSE:DEN-EQ",
    "NSE:JAGRAN-EQ",
    "NSE:BALAJITELE-EQ",
    "NSE:SHEMAROO-EQ",
    "NSE:NAVNETEDUL-EQ",
    "NSE:PFOCUS-EQ",
    # Travel, hotels, and agri.
    "NSE:LEMONTREE-EQ",
    "NSE:THOMASCOOK-EQ",
    "NSE:YATRA-EQ",
    "NSE:JISLJALEQS-EQ",
    # Auto ancillaries.
    "NSE:MENONBE-EQ",
    "NSE:MUNJALAU-EQ",
    "NSE:JTEKTINDIA-EQ",
    "NSE:MMFL-EQ",
    "NSE:OMAXAUTO-EQ",
    "NSE:AUTOIND-EQ",
    # Chemicals, fertilisers, and pharma.
    "NSE:RAIN-EQ",
    "NSE:DCW-EQ",
    "NSE:SPIC-EQ",
    "NSE:WANBURY-EQ",
    "NSE:JAYAGROGN-EQ",
    "NSE:ALEMBICLTD-EQ",
    "NSE:KOPRAN-EQ",
    # Textiles and apparel.
    "NSE:KITEX-EQ",
    "NSE:VIPCLOTHNG-EQ",
    "NSE:LOVABLE-EQ",
    "NSE:BANSWRAS-EQ",
    "NSE:SUTLEJTEX-EQ",
    # Building materials.
    "NSE:ORIENTPPR-EQ",
    "NSE:BIGBLOC-EQ",
    "NSE:PRSMJOHNSN-EQ",
    # Sugar.
    "NSE:RENUKA-EQ",
    # Telecom PSU and energy exploration.
    "NSE:ITI-EQ",
    "NSE:HINDOILEXP-EQ",
    # Metals and energy distribution.
    "NSE:MUKANDLTD-EQ",
    "NSE:CONFIPET-EQ",
    # Second batch of popular sub-200 names (power, infra, media, chemicals,
    # textiles, building materials, finance). All verified against FYERS.
    "NSE:SEPC-EQ",
    "NSE:RTNPOWER-EQ",
    "NSE:SUNFLAG-EQ",
    "NSE:STCINDIA-EQ",
    "NSE:SPENCERS-EQ",
    "NSE:UFO-EQ",
    "NSE:INOXGREEN-EQ",
    "NSE:IKIO-EQ",
    "NSE:NIITLTD-EQ",
    "NSE:APTECHT-EQ",
    "NSE:WATERBASE-EQ",
    "NSE:GPTHEALTH-EQ",
    "NSE:KTKBANK-EQ",
    "NSE:MUTHOOTMF-EQ",
    "NSE:RSWM-EQ",
    "NSE:PRECOT-EQ",
    "NSE:HIMATSEIDE-EQ",
    "NSE:GHCLTEXTIL-EQ",
    "NSE:ORIENTCEM-EQ",
    "NSE:SHREDIGCEM-EQ",
    "NSE:KCP-EQ",
    "NSE:ASIANTILES-EQ",
    "NSE:PTL-EQ",
    "NSE:DPWIRES-EQ",
    "NSE:RESPONIND-EQ",
    "NSE:RAMCOIND-EQ",
    "NSE:TVTODAY-EQ",
    "NSE:IMAGICAA-EQ",
    "NSE:DELTACORP-EQ",
    "NSE:SNOWMAN-EQ",
    "NSE:COMPUSOFT-EQ",
    "NSE:SAGILITY-EQ",
    "NSE:UGROCAP-EQ",
    "NSE:PPAP-EQ",
    "NSE:DIGISPICE-EQ",
    "NSE:EKC-EQ",
    "NSE:GPIL-EQ",
    "NSE:RUCHIRA-EQ",
    "NSE:DWARKESH-EQ",
    "NSE:ANDHRSUGAR-EQ",
    "NSE:UTTAMSUGAR-EQ",
    "NSE:GUJALKALI-EQ",
    "NSE:GOLDIAM-EQ",
    "NSE:MANINFRA-EQ",
    "NSE:NITINSPIN-EQ",
    "NSE:RAJSREESUG-EQ",
    "NSE:IDEA-EQ",
    "NSE:YESBANK-EQ",
    "NSE:IDFCFIRSTB-EQ",
    "NSE:PNB-EQ",
    "NSE:IOB-EQ",
    "NSE:MAHABANK-EQ",
    "NSE:CENTRALBK-EQ",
    "NSE:UCOBANK-EQ",
    "NSE:J&KBANK-EQ",
    "NSE:SOUTHBANK-EQ",
    "NSE:CUB-EQ",
    "NSE:BANDHANBNK-EQ",
    "NSE:CANBK-EQ",
    "NSE:UNIONBANK-EQ",
    "NSE:SAIL-EQ",
    "NSE:TATASTEEL-EQ",
    "NSE:NATIONALUM-EQ",
    "NSE:CESC-EQ",
    "NSE:NHPC-EQ",
    "NSE:SJVN-EQ",
    "NSE:SUZLON-EQ",
    "NSE:INOXWIND-EQ",
    "NSE:RPOWER-EQ",
    "NSE:JPPOWER-EQ",
    "NSE:IRFC-EQ",
    "NSE:IREDA-EQ",
    "NSE:GAIL-EQ",
    "NSE:IOC-EQ",
    "NSE:TTML-EQ",
    "NSE:ONMOBILE-EQ",
    "NSE:NETWORK18-EQ",
    "NSE:DBREALTY-EQ",
    "NSE:ZEEL-EQ",
    "NSE:MOTHERSON-EQ",
    "NSE:NBCC-EQ",
    "NSE:IRCON-EQ",
    "NSE:NCC-EQ",
    "NSE:SAURASHCEM-EQ",
    "NSE:LLOYDSME-EQ",
    "NSE:PNCINFRA-EQ",
    "NSE:MTNL-EQ",
    "NSE:RITES-EQ",
    "NSE:IRB-EQ",
    "NSE:ASHOKA-EQ",
    "NSE:HCC-EQ",
    "NSE:ALLCARGO-EQ",
    "NSE:RVNL-EQ",
    "NSE:JWL-EQ",
    "NSE:EDELWEISS-EQ",
    "NSE:UJJIVANSFB-EQ",
    "NSE:EQUITASBNK-EQ",
    "NSE:SURYODAY-EQ",
    "NSE:DCBBANK-EQ",
    "NSE:RCF-EQ",
    "NSE:NFL-EQ",
    "NSE:MADRASFERT-EQ",
    "NSE:GSFC-EQ",
    "NSE:TRIDENT-EQ",
    "NSE:WELSPUNLIV-EQ",
    "NSE:VARDHACRLC-EQ",
    "NSE:TEXRAIL-EQ",
    "NSE:HIKAL-EQ",
    "NSE:KELLTONTEC-EQ",
    "NSE:TRIGYN-EQ",
    "NSE:VSTIND-EQ",
    "NSE:DHAMPURSUG-EQ",
    "NSE:MANAPPURAM-EQ",
    "NSE:FEDERALBNK-EQ",
    "NSE:HUDCO-EQ",
    "NSE:NMDC-EQ",
    "NSE:BHEL-EQ",
    "NSE:ASHOKLEY-EQ",
    "NSE:JINDALSAW-EQ",
    "NSE:KARURVYSYA-EQ",
    "NSE:RBLBANK-EQ",
    "NSE:BIOCON-EQ",
    "NSE:JIOFIN-EQ",
    "NSE:TATAPOWER-EQ",
)


def normalize_mode(mode: str) -> str:
    """Return a supported universe mode or raise ``ValueError``."""
    normalized = mode.strip().lower()
    if normalized not in UNIVERSE_MODES:
        raise ValueError(
            f"unknown universe mode {mode!r}; expected one of {', '.join(UNIVERSE_MODES)}"
        )
    return normalized


def resolve_master(settings: Settings) -> tuple[str, ...]:
    """The configured master list, or the built-in liquid list when unset."""
    return tuple(settings.universe_master) or MASTER_UNIVERSE


def select_universe(
    master: tuple[str, ...] | list[str],
    prices: Mapping[str, Decimal],
    mode: str,
    size: int = DEFAULT_UNIVERSE_SIZE,
    cheap_max_price_inr: Decimal = CHEAP_MAX_PRICE_INR,
) -> list[str]:
    """Pick up to ``size`` symbols from ``master`` for the requested mode.

    Only symbols with a positive live price are considered. Master order is
    preserved, so the result is stable for an unchanged price map. When one
    bucket cannot fill the board, the other bucket tops it up rather than
    returning a short board.
    """
    normalized = normalize_mode(mode)
    if size <= 0:
        return []
    cap = cheap_max_price_inr
    seen: set[str] = set()
    cheap: list[str] = []
    expensive: list[str] = []
    for symbol in master:
        if symbol in seen:
            continue
        seen.add(symbol)
        price = prices.get(symbol)
        if price is None or price <= 0:
            continue
        if price < cap:
            cheap.append(symbol)
        else:
            expensive.append(symbol)

    if normalized == "cheap":
        picked = cheap[:size]
        if len(picked) < size:
            picked.extend(expensive[: size - len(picked)])
        return picked
    if normalized == "expensive":
        picked = expensive[:size]
        if len(picked) < size:
            picked.extend(cheap[: size - len(picked)])
        return picked

    cheap_target = size // 2
    cheap_picked = cheap[:cheap_target]
    expensive_target = size - len(cheap_picked)
    expensive_picked = expensive[:expensive_target]
    if len(expensive_picked) < expensive_target:
        cheap_picked = cheap[: size - len(expensive_picked)]
    if len(cheap_picked) < cheap_target:
        expensive_picked = expensive[: size - len(cheap_picked)]
    return cheap_picked + expensive_picked


def price_allowed(
    mode: str,
    price: Decimal,
    cheap_max_price_inr: Decimal = CHEAP_MAX_PRICE_INR,
) -> bool:
    """Whether a price still belongs to the active bucket.

    ``mixed`` accepts everything; the other modes drop names that crossed the
    cap between universe refreshes. Held symbols bypass this at the session
    layer so an exit is never stranded by a price move.
    """
    normalized = normalize_mode(mode)
    if normalized == "mixed":
        return True
    if normalized == "cheap":
        return price < cheap_max_price_inr
    return price >= cheap_max_price_inr
