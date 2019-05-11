from argparse import ArgumentParser, Namespace
from itertools import chain
from bankroll import Activity, Instrument, Stock, Position, Trade, Cash, MarketDataProvider, DataAggregator, analysis
from bankroll.brokers import *
from bankroll.configuration import Configuration, Settings, addSettingsToArgumentGroup
from progress.bar import Bar
from typing import Dict, Iterable, List, Optional

import logging

parser = ArgumentParser(prog='bankroll')

parser.add_argument(
    '--lenient',
    help=
    'Attempt to ignore invalid data instead of erroring out. May not be supported for all data sources.',
    default=False,
    action='store_true')
parser.add_argument('--no-lenient', dest='lenient', action='store_false')
parser.add_argument('-v',
                    '--verbose',
                    help='More logging.',
                    dest='verbose',
                    default=False,
                    action='store_true')
parser.add_argument(
    '--config',
    help=
    'Path to an INI file specifying configuration options. Can be specified multiple times.',
    action='append')

ibGroup = parser.add_argument_group(
    'IB', 'Options for importing data from Interactive Brokers.')
readIBSettings = addSettingsToArgumentGroup(ibkr.Settings, ibGroup)

fidelityGroup = parser.add_argument_group(
    'Fidelity',
    'Options for importing data from local files in Fidelity\'s CSV export format.'
)
readFidelitySettings = addSettingsToArgumentGroup(fidelity.Settings,
                                                  fidelityGroup)

schwabGroup = parser.add_argument_group(
    'Schwab',
    'Options for importing data from local files in Charles Schwab\'s CSV export format.'
)
readSchwabSettings = addSettingsToArgumentGroup(schwab.Settings, schwabGroup)

vanguardGroup = parser.add_argument_group(
    'Vanguard',
    'Options for importing data from local files in Vanguard\'s CSV export format.'
)
readVanguardSettings = addSettingsToArgumentGroup(vanguard.Settings,
                                                  vanguardGroup)


def printPositions(data: DataAggregator, args: Namespace) -> None:
    values: Dict[Position, Cash] = {}
    if args.live_value:
        if data.dataProvider:
            values = analysis.liveValuesForPositions(
                data.positions,
                dataProvider=data.dataProvider,
                progressBar=Bar('Loading market data for positions'))
        else:
            logging.error(
                'Live data connection required to fetch market values')

    for p in sorted(data.positions, key=lambda p: p.instrument):
        print(p)

        if p in values:
            print(f'\tMarket value: {values[p]}')
        elif args.live_value:
            logging.warning(f'Could not fetch market value for {p.instrument}')

        if not isinstance(p.instrument, Stock):
            continue

        print(f'\tCost basis: {p.costBasis}')

        if args.realized_basis:
            realizedBasis = analysis.realizedBasisForSymbol(
                p.instrument.symbol, activity=data.activity)
            print(f'\tRealized basis: {realizedBasis}')


def printActivity(data: DataAggregator, args: Namespace) -> None:
    for t in sorted(data.activity, key=lambda t: t.date, reverse=True):
        print(t)


commands = {
    'positions': printPositions,
    'activity': printActivity,
}

subparsers = parser.add_subparsers(dest='command', help='What to inspect')

positionsParser = subparsers.add_parser(
    'positions',
    help='Operations upon the imported list of portfolio positions')
positionsParser.add_argument(
    '--realized-basis',
    help='Calculate realized basis for stock positions',
    default=False,
    action='store_true')
positionsParser.add_argument(
    '--live-value',
    help='Fetch live, mark-to-market value of positions',
    default=False,
    action='store_true')

activityParser = subparsers.add_parser(
    'activity', help='Operations upon imported portfolio activity')


def main() -> None:
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    config = Configuration(
        chain(Configuration.defaultSearchPaths,
              args.config if args.config else []))

    if not args.command:
        parser.print_usage()
        quit(1)

    mergedSettings: Dict[Settings, str] = dict(
        chain(
            readFidelitySettings(config, args).items(),
            readSchwabSettings(config, args).items(),
            readVanguardSettings(config, args).items(),
            readIBSettings(config, args).items()))

    data = DataAggregator(mergedSettings).loadData(lenient=args.lenient)
    commands[args.command](data, args)


if __name__ == '__main__':
    main()