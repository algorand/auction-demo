from typing import Tuple
from time import time

from algosdk.v2client.algod import AlgodClient
from algosdk.future import transaction
from algosdk import account, encoding

from pyteal import compileTeal, Mode

from .testing.setup import getAlgodClient
from .testing.resources import (
    waitForTransaction,
    fundAccount,
    getTemporaryAccount,
    fullyCompileContract,
)

from .auction import approval_program, clear_state_program

APPROVAL_PROGRAM = b""
CLEAR_STATE_PROGRAM = b""


def getContracts(client: AlgodClient) -> Tuple[bytes, bytes]:
    global APPROVAL_PROGRAM
    global CLEAR_STATE_PROGRAM

    if len(APPROVAL_PROGRAM) == 0:
        APPROVAL_PROGRAM = fullyCompileContract(client, approval_program())
        CLEAR_STATE_PROGRAM = fullyCompileContract(client, clear_state_program())

    return APPROVAL_PROGRAM, CLEAR_STATE_PROGRAM


def test_create():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)

    approval, clear = getContracts(client)

    globalSchema = transaction.StateSchema(num_uints=7, num_byte_slices=2)
    localSchema = transaction.StateSchema(num_uints=0, num_byte_slices=0)

    _, seller_addr = account.generate_account()
    nft_id = 1  # fake ID
    start_time = int(time()) + 10  # start time is 10 seconds in the future
    end_time = start_time + 60  # end time is 1 minute after start
    reserve_amount = 1_000_000  # 1 Algo
    min_bid_increment = 100_000  # 0.1 Algo

    app_args = [
        encoding.decode_address(seller_addr),
        nft_id.to_bytes(8, "big"),
        start_time.to_bytes(8, "big"),
        end_time.to_bytes(8, "big"),
        reserve_amount.to_bytes(8, "big"),
        min_bid_increment.to_bytes(8, "big"),
    ]

    txn = transaction.ApplicationCreateTxn(
        sender=creator.getAddress(),
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=approval,
        clear_program=clear,
        global_schema=globalSchema,
        local_schema=localSchema,
        app_args=app_args,
        sp=client.suggested_params(),
    )

    signedTxn = txn.sign(creator.getPrivateKey())

    client.send_transaction(signedTxn)

    waitForTransaction(client, signedTxn.get_txid())

    # TODO: verify global state is correctly set
