from datetime import datetime
from decimal import Decimal
from model import Currency, Cash, Instrument, Stock, Bond, Option, OptionType, FutureOption, Future, Forex, Position, TradeFlags, Trade, LiveDataProvider, Quote
from parsetools import lenientParse
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, NamedTuple, Optional, Type

import ib_insync as IB
import logging
import math
import re


def parseOption(symbol: str, currency: Currency,
                cls: Type[Option] = Option) -> Option:
    # https://en.wikipedia.org/wiki/Option_symbol#The_OCC_Option_Symbol
    match = re.match(
        r'^(?P<underlying>.{6})(?P<date>\d{6})(?P<putCall>P|C)(?P<strike>\d{8})$',
        symbol)
    if not match:
        raise ValueError('Could not parse IB option symbol: {}'.format(symbol))

    if match['putCall'] == 'P':
        optionType = OptionType.PUT
    else:
        optionType = OptionType.CALL

    return cls(underlying=match['underlying'].rstrip(),
               currency=currency,
               optionType=optionType,
               expiration=datetime.strptime(match['date'], '%y%m%d').date(),
               strike=Decimal(match['strike']) / 1000)


def extractPosition(p: IB.Position) -> Position:
    instrumentsByTag: Dict[str, Callable[[str, Currency], Instrument]] = {
        "STK": Stock,
        "BOND": lambda s, c: Bond(s, c, validateSymbol=False),
        "OPT": parseOption,
        "FUT": Future,
        "CASH": Forex,
        # TODO: FOP
    }

    qty = Decimal(p.position)
    costBasis = Decimal(p.avgCost) * qty

    return Position(instrument=instrumentsByTag[p.contract.secType](
        p.contract.localSymbol, Currency[p.contract.currency]),
                    quantity=qty,
                    costBasis=Cash(currency=Currency[p.contract.currency],
                                   quantity=costBasis))


def downloadPositions(ib: IB.IB, lenient: bool) -> List[Position]:
    return list(
        lenientParse(ib.positions(),
                     transform=extractPosition,
                     lenient=lenient))


class IBTradeConfirm(NamedTuple):
    accountId: str
    acctAlias: str
    model: str
    currency: str
    assetCategory: str
    symbol: str
    description: str
    conid: str
    securityID: str
    securityIDType: str
    cusip: str
    isin: str
    listingExchange: str
    underlyingConid: str
    underlyingSymbol: str
    underlyingSecurityID: str
    underlyingListingExchange: str
    issuer: str
    multiplier: str
    strike: str
    expiry: str
    putCall: str
    principalAdjustFactor: str
    transactionType: str
    tradeID: str
    orderID: str
    execID: str
    brokerageOrderID: str
    orderReference: str
    volatilityOrderLink: str
    clearingFirmID: str
    origTradePrice: str
    origTradeDate: str
    origTradeID: str
    orderTime: str
    dateTime: str
    reportDate: str
    settleDate: str
    tradeDate: str
    exchange: str
    buySell: str
    quantity: str
    price: str
    amount: str
    proceeds: str
    commission: str
    brokerExecutionCommission: str
    brokerClearingCommission: str
    thirdPartyExecutionCommission: str
    thirdPartyClearingCommission: str
    thirdPartyRegulatoryCommission: str
    otherCommission: str
    commissionCurrency: str
    tax: str
    code: str
    orderType: str
    levelOfDetail: str
    traderID: str
    isAPIOrder: str
    allocatedTo: str
    accruedInt: str


def parseFutureOptionTrade(trade: IBTradeConfirm) -> Instrument:
    if trade.putCall == 'C':
        optionType = OptionType.CALL
    elif trade.putCall == 'P':
        optionType = OptionType.PUT
    else:
        raise ValueError(
            'Unexpected value for putCall in IB trade: {}'.format(trade))

    return FutureOption(symbol=trade.symbol,
                        currency=Currency[trade.currency],
                        underlying=trade.underlyingSymbol,
                        optionType=optionType,
                        expiration=datetime.strptime(trade.expiry,
                                                     '%Y%m%d').date(),
                        strike=Decimal(trade.strike))


def parseTradeConfirm(trade: IBTradeConfirm) -> Trade:
    instrumentsByTag: Dict[str, Callable[[IBTradeConfirm], Instrument]] = {
        'STK':
        lambda t: Stock(t.symbol, currency=Currency[t.currency]),
        'BOND':
        lambda t: Bond(
            t.symbol, currency=Currency[t.currency], validateSymbol=False),
        'OPT':
        lambda t: parseOption(t.symbol, currency=Currency[t.currency]),
        'FUT':
        lambda t: Future(t.symbol, currency=Currency[t.currency]),
        'CASH':
        lambda t: Forex(t.symbol, currency=Currency[t.currency]),
        'FOP':
        parseFutureOptionTrade,
    }

    flagsByCode = {
        'O': TradeFlags.OPEN,
        'C': TradeFlags.CLOSE,
        'A': TradeFlags.ASSIGNED_OR_EXERCISED,
        'Ex': TradeFlags.ASSIGNED_OR_EXERCISED,
        'Ep': TradeFlags.EXPIRED,
        'R': TradeFlags.DRIP,

        # Partial execution
        'P': TradeFlags.NONE,

        # Unknown code, spotted on a complex futures trade
        'D': TradeFlags.NONE,
    }

    codes = trade.code.split(';')
    flags = TradeFlags.NONE
    for c in codes:
        if c == '':
            continue

        flags |= flagsByCode[c]

    # Codes are not always populated with open/close, not sure why
    if flags & (TradeFlags.OPEN | TradeFlags.CLOSE) == TradeFlags.NONE:
        if trade.buySell == 'BUY':
            flags |= TradeFlags.OPEN
        else:
            flags |= TradeFlags.CLOSE

    return Trade(
        date=datetime.strptime(trade.tradeDate, '%Y%m%d'),
        instrument=instrumentsByTag[trade.assetCategory](trade),
        quantity=Decimal(trade.quantity),
        amount=Cash(currency=Currency(trade.currency),
                    quantity=Decimal(trade.proceeds)),
        fees=Cash(currency=Currency(trade.commissionCurrency),
                  quantity=-(Decimal(trade.commission) + Decimal(trade.tax))),
        flags=flags)


def tradesFromReport(report: IB.FlexReport, lenient: bool) -> List[Trade]:
    return list(
        lenientParse(
            (IBTradeConfirm(**t.__dict__)
             for t in report.extract('TradeConfirm', parseNumbers=False)),
            transform=parseTradeConfirm,
            lenient=lenient))


def parseTrades(path: Path, lenient: bool = False) -> List[Trade]:
    return tradesFromReport(IB.FlexReport(path=path), lenient=lenient)


def downloadTrades(token: str, queryID: int,
                   lenient: bool = False) -> List[Trade]:
    return tradesFromReport(IB.FlexReport(token=token, queryId=queryID),
                            lenient=lenient)


def stockContract(stock: Stock) -> IB.Contract:
    return IB.Stock(symbol=stock.symbol, currency=stock.currency.value)


def bondContract(bond: Bond) -> IB.Contract:
    return IB.Bond(symbol=bond.symbol, currency=bond.currency.value)


def optionContract(option: Option) -> IB.Contract:
    lastTradeDate = option.expiration.strftime('%Y%m%d')

    return IB.Option(symbol=option.symbol,
                     currency=option.currency.value,
                     lastTradeDateOrContractMonth=lastTradeDate,
                     right=option.optionType.value)


def fopContract(option: FutureOption) -> IB.Contract:
    lastTradeDate = option.expiration.strftime('%Y%m%d')

    return IB.FuturesOption(symbol=option.symbol,
                            currency=option.currency.value,
                            lastTradeDateOrContractMonth=lastTradeDate,
                            right=option.optionType.value)


def futuresContract(future: Future) -> IB.Contract:
    return IB.Future(symbol=future.symbol, currency=future.currency.value)


def forexContract(forex: Forex) -> IB.Contract:
    return IB.Forex(pair=forex.symbol, currency=forex.currency.value)


def contract(instrument: Instrument) -> IB.Contract:
    if isinstance(instrument, Stock):
        return stockContract(instrument)
    elif isinstance(instrument, Bond):
        return bondContract(instrument)
    elif isinstance(instrument, FutureOption):
        return fopContract(instrument)
    elif isinstance(instrument, Option):
        return optionContract(instrument)
    elif isinstance(instrument, Future):
        return futuresContract(instrument)
    elif isinstance(instrument, Forex):
        return forexContract(instrument)
    else:
        raise ValueError('Unexpected type of instrument: {}'.format(
            repr(instrument)))


class IBDataProvider(LiveDataProvider):
    def __init__(self, client: IB.IB):
        self._client = client
        super().__init__()

    def fetchQuote(self, instrument: Instrument) -> Quote:
        con = contract(instrument)
        self._client.qualifyContracts(con)

        ticker = self._client.reqTickers(con)[0]
        logging.info('Received ticker: {}'.format(repr(ticker)))

        bid: Optional[Cash] = None
        ask: Optional[Cash] = None
        last: Optional[Cash] = None

        if ticker.bid and math.isfinite(ticker.bid):
            bid = Cash(currency=instrument.currency,
                       quantity=Decimal(ticker.bid))
        if ticker.ask and math.isfinite(ticker.ask):
            ask = Cash(currency=instrument.currency,
                       quantity=Decimal(ticker.ask))
        if ticker.last and math.isfinite(ticker.last):
            last = Cash(currency=instrument.currency,
                        quantity=Decimal(ticker.last))

        return Quote(bid=bid, ask=ask, last=last)
