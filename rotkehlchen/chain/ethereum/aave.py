from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional, Union

from typing_extensions import Literal

from rotkehlchen.accounting.structures import Balance
from rotkehlchen.assets.asset import EthereumToken
from rotkehlchen.constants.ethereum import AAVE_LENDING_POOL, ATOKEN_ABI, ZERO_ADDRESS
from rotkehlchen.db.dbhandler import DBHandler
from rotkehlchen.fval import FVal
from rotkehlchen.history.price import query_usd_price_zero_if_error
from rotkehlchen.premium.premium import Premium
from rotkehlchen.serialization.deserialize import deserialize_blocknumber
from rotkehlchen.typing import ChecksumEthAddress, Timestamp
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.misc import hex_or_bytes_to_address, hex_or_bytes_to_int

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.manager import EthereumManager

ATOKEN_TO_DEPLOYED_BLOCK = {
    'aETH': 9241088,
    'aDAI': 9241063,
    'aUSDC': 9241071,
    'aSUSD': 9241077,
    'aTUSD': 9241068,
    'aUSDT': 9241076,
    'aBUSD': 9747321,
    'aBAT': 9241085,
    'aKNC': 9241097,
    'aLEND': 9241081,
    'aLINK': 9241091,
    'aMANA': 9241110,
    'aMKR': 9241106,
    'aREP': 9241100,
    'aSNX': 9241118,
    'aWBTC': 9241225,
    'aZRX': 9241114,
}
ATOKENS_LIST = [EthereumToken(x) for x in ATOKEN_TO_DEPLOYED_BLOCK]


class AaveInterestPayment(NamedTuple):
    """An interest payment from an aToken.

    The type of token not included here since these are in a mapping with a list
    per aToken so it would be redundant
    """
    balance: Balance
    block_number: int
    timestamp: Timestamp


class Aave():
    """Aave integration module

    https://docs.aave.com/developers/developing-on-aave/the-protocol/
    """

    def __init__(
            self,
            ethereum_manager: 'EthereumManager',
            database: DBHandler,
            premium: Optional[Premium],
            msg_aggregator: MessagesAggregator,
    ) -> None:
        self.ethereum = ethereum_manager
        self.database = database
        self.msg_aggregator = msg_aggregator
        self.premium = premium

    def get_lending_profit_events_for_address(
            self,
            user_address: ChecksumEthAddress,
            given_from_block: Optional[int] = None,
            given_to_block: Optional[int] = None,
            atokens_list: Optional[List[EthereumToken]] = None,
    ) -> Dict[EthereumToken, List]:
        # Get all deposit events for the address
        from_block = AAVE_LENDING_POOL.deployed_block if given_from_block is None else given_from_block  # noqa: E501
        to_block: Union[int, Literal['latest']] = 'latest' if given_to_block is None else given_to_block  # noqa: E501
        argument_filters = {
            '_user': user_address,
        }
        deposit_events = self.ethereum.get_logs(
            contract_address=AAVE_LENDING_POOL.address,
            abi=AAVE_LENDING_POOL.abi,
            event_name='Deposit',
            argument_filters=argument_filters,
            from_block=from_block,
            to_block=to_block,
        )

        # now for each atoken get all mint events and pass then to profit calculation
        tokens = atokens_list if atokens_list is not None else ATOKENS_LIST
        profit_map = {}
        for token in tokens:
            profit_map[token] = self.get_profit_events_for_atoken_and_address(
                user_address=user_address,
                atoken=token,
                deposit_events=deposit_events,
                given_from_block=given_from_block,
                given_to_block=given_to_block,
            )

        return profit_map

    def get_profit_events_for_atoken_and_address(
            self,
            user_address: ChecksumEthAddress,
            atoken: EthereumToken,
            deposit_events: List[Dict[str, Any]],
            given_from_block: Optional[int] = None,
            given_to_block: Optional[int] = None,
    ) -> List[AaveInterestPayment]:
        from_block = ATOKEN_TO_DEPLOYED_BLOCK[atoken.identifier] if given_from_block is None else given_from_block  # noqa: E501
        to_block: Union[int, Literal['latest']] = 'latest' if given_to_block is None else given_to_block  # noqa: E501
        argument_filters = {
            'from': ZERO_ADDRESS,
            'to': user_address,
        }
        mint_events = self.ethereum.get_logs(
            contract_address=atoken.ethereum_address,
            abi=ATOKEN_ABI,
            event_name='Transfer',
            argument_filters=argument_filters,
            from_block=from_block,
            to_block=to_block,
        )
        mint_data = set()
        for event in mint_events:
            amount = hex_or_bytes_to_int(event['data'])
            if amount == 0:
                continue  # first mint can be for 0. Ignore
            mint_data.add((
                deserialize_blocknumber(event['blockNumber']),
                amount,
                self.ethereum.get_event_timestamp(event),
            ))

        normal_token_symbol = atoken.identifier[1:]
        if normal_token_symbol == 'ETH':
            reserve_address = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'
            decimals = 18
        else:
            token = EthereumToken(normal_token_symbol)
            reserve_address = token.ethereum_address
            decimals = token.decimals
        for event in deposit_events:
            if hex_or_bytes_to_address(event['topics'][1]) == reserve_address:
                # first 32 bytes of the data are the amount
                deposit = hex_or_bytes_to_int(event['data'][:66])
                block_number = deserialize_blocknumber(event['blockNumber'])
                timestamp = self.ethereum.get_event_timestamp(event)
                # If there is a corresponding deposit event remove the minting event data
                if (block_number, deposit, timestamp) in mint_data:
                    mint_data.remove((block_number, deposit, timestamp))

        profit_events = []
        for data in mint_data:
            usd_price = query_usd_price_zero_if_error(
                asset=atoken,
                time=data[2],
                location='aave interest profit',
                msg_aggregator=self.msg_aggregator,
            )
            interest_amount = data[1] / (FVal(10) ** FVal(decimals))
            profit_events.append(AaveInterestPayment(
                balance=Balance(
                    amount=interest_amount,
                    usd_value=interest_amount * usd_price,
                ),
                block_number=data[0],
                timestamp=data[2],
            ))

        profit_events.sort(key=lambda event: event.timestamp)
        return profit_events
