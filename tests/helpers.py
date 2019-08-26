from decimal import Decimal
from bankroll.model import Cash, Currency


def cashUSD(amount: Decimal) -> Cash:
    return Cash(currency=Currency.USD, quantity=amount)
