from typing import Tuple, List
from time import time, sleep

import pytest

from algosdk.v2client.algod import AlgodClient
from algosdk.future import transaction
from algosdk import account, encoding

from pyteal import compileTeal, Mode

from .testing.account import Account
from .testing.setup import getAlgodClient
from .testing.resources import (
    waitForTransaction,
    fundAccount,
    getTemporaryAccount,
    createDummyAsset,
    optInToAsset,
    fullyCompileContract,
    getAppGlobalState,
    getAppAddress,
    getBalances,
    getLastBlockTimestamp,
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


def createAuctionApp(
    client: AlgodClient,
    sender: Account,
    seller: str,
    nftID: int,
    startTime: int,
    endTime: int,
    reserve: int,
    minBidIncrement: int,
) -> int:
    approval, clear = getContracts(client)

    globalSchema = transaction.StateSchema(num_uints=7, num_byte_slices=2)
    localSchema = transaction.StateSchema(num_uints=0, num_byte_slices=0)

    app_args = [
        encoding.decode_address(seller),
        nftID.to_bytes(8, "big"),
        startTime.to_bytes(8, "big"),
        endTime.to_bytes(8, "big"),
        reserve.to_bytes(8, "big"),
        minBidIncrement.to_bytes(8, "big"),
    ]

    txn = transaction.ApplicationCreateTxn(
        sender=sender.getAddress(),
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=approval,
        clear_program=clear,
        global_schema=globalSchema,
        local_schema=localSchema,
        app_args=app_args,
        sp=client.suggested_params(),
    )

    signedTxn = txn.sign(sender.getPrivateKey())

    client.send_transaction(signedTxn)

    response = waitForTransaction(client, signedTxn.get_txid())
    assert response.applicationIndex is not None and response.applicationIndex > 0
    return response.applicationIndex


def setupAuctionApp(
    client: AlgodClient,
    appID: int,
    funder: Account,
    nftHolder: Account,
    nftID: int,
    nftAmount: int,
) -> None:
    appAddr = getAppAddress(appID)

    suggestedParams = client.suggested_params()

    fundingAmount = (
        # min account balance
        100_000
        # additional min balance to opt into NFT
        + 100_000
        # 3 * min txn fee
        + 3 * 1_000
    )

    fundAppTxn = transaction.PaymentTxn(
        sender=funder.getAddress(),
        receiver=appAddr,
        amt=fundingAmount,
        sp=suggestedParams,
    )

    setupTxn = transaction.ApplicationCallTxn(
        sender=funder.getAddress(),
        index=appID,
        on_complete=transaction.OnComplete.NoOpOC,
        app_args=[b"setup"],
        foreign_assets=[nftID],
        sp=suggestedParams,
    )

    fundNftTxn = transaction.AssetTransferTxn(
        sender=nftHolder.getAddress(),
        receiver=appAddr,
        index=nftID,
        amt=nftAmount,
        sp=suggestedParams,
    )

    transaction.assign_group_id([fundAppTxn, setupTxn, fundNftTxn])

    signedFundAppTxn = fundAppTxn.sign(funder.getPrivateKey())
    signedSetupTxn = setupTxn.sign(funder.getPrivateKey())
    signedFundNftTxn = fundNftTxn.sign(nftHolder.getPrivateKey())

    client.send_transactions([signedFundAppTxn, signedSetupTxn, signedFundNftTxn])

    waitForTransaction(client, signedFundAppTxn.get_txid())


def placeBid(client: AlgodClient, appID: int, bidder: Account, bidAmount: int) -> None:
    appAddr = getAppAddress(appID)
    appGlobalState = getAppGlobalState(client, appID)

    nftID = appGlobalState[b"nft_id"]

    if any(appGlobalState[b"bid_account"]):
        # if "bid_account" is not the zero address
        prevBidLeader = encoding.encode_address(appGlobalState[b"bid_account"])
    else:
        prevBidLeader = None

    suggestedParams = client.suggested_params()

    payTxn = transaction.PaymentTxn(
        sender=bidder.getAddress(),
        receiver=appAddr,
        amt=bidAmount,
        sp=suggestedParams,
    )

    appCallTxn = transaction.ApplicationCallTxn(
        sender=bidder.getAddress(),
        index=appID,
        on_complete=transaction.OnComplete.NoOpOC,
        app_args=[b"bid"],
        foreign_assets=[nftID],
        # must include the previous lead bidder here to the app can refund that bidder's payment
        accounts=[prevBidLeader] if prevBidLeader is not None else [],
        sp=suggestedParams,
    )

    transaction.assign_group_id([payTxn, appCallTxn])

    signedPayTxn = payTxn.sign(bidder.getPrivateKey())
    signedAppCallTxn = appCallTxn.sign(bidder.getPrivateKey())

    client.send_transactions([signedPayTxn, signedAppCallTxn])

    waitForTransaction(client, appCallTxn.get_txid())


def closeAuction(client: AlgodClient, appID: int, closer: Account):
    appGlobalState = getAppGlobalState(client, appID)

    nftID = appGlobalState[b"nft_id"]

    accounts: List[str] = [encoding.encode_address(appGlobalState[b"seller"])]

    if any(appGlobalState[b"bid_account"]):
        # if "bid_account" is not the zero address
        accounts.append(encoding.encode_address(appGlobalState[b"bid_account"]))

    deleteTxn = transaction.ApplicationDeleteTxn(
        sender=closer.getAddress(),
        index=appID,
        accounts=accounts,
        foreign_assets=[nftID],
        sp=client.suggested_params(),
    )
    signedDeleteTxn = deleteTxn.sign(closer.getPrivateKey())

    client.send_transaction(signedDeleteTxn)

    waitForTransaction(client, signedDeleteTxn.get_txid())


def test_create():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    _, seller_addr = account.generate_account()  # random address

    nftID = 1  # fake ID
    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 60  # end time is 1 minute after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller_addr,
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    actual = getAppGlobalState(client, appID)
    expected = {
        b"seller": encoding.decode_address(seller_addr),
        b"nft_id": nftID,
        b"start": startTime,
        b"end": endTime,
        b"reserve_amount": reserve,
        b"min_bid_inc": increment,
        b"bid_account": bytes(32),  # decoded zero address
    }

    assert actual == expected


def test_setup():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 60  # end time is 1 minute after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    actualState = getAppGlobalState(client, appID)
    expectedState = {
        b"seller": encoding.decode_address(seller.getAddress()),
        b"nft_id": nftID,
        b"start": startTime,
        b"end": endTime,
        b"reserve_amount": reserve,
        b"min_bid_inc": increment,
        b"bid_account": bytes(32),  # decoded zero address
    }

    assert actualState == expectedState

    actualBalances = getBalances(client, getAppAddress(appID))
    expectedBalances = {0: 2 * 100_000 + 2 * 1_000, nftID: nftAmount}

    assert actualBalances == expectedBalances


def test_first_bid_before_start():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 5 * 60  # start time is 5 minutes in the future
    endTime = startTime + 60  # end time is 1 minute after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    bidder = getTemporaryAccount(client)

    _, lastRoundTime = getLastBlockTimestamp(client)
    assert lastRoundTime < startTime

    with pytest.raises(Exception):
        bidAmount = 500_000  # 0.5 Algos
        placeBid(client=client, appID=appID, bidder=bidder, bidAmount=bidAmount)


def test_first_bid():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 60  # end time is 1 minute after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    bidder = getTemporaryAccount(client)

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < startTime:
        sleep(startTime - lastRoundTime)

    bidAmount = 500_000  # 0.5 Algos
    placeBid(client=client, appID=appID, bidder=bidder, bidAmount=bidAmount)

    actualState = getAppGlobalState(client, appID)
    expectedState = {
        b"seller": encoding.decode_address(seller.getAddress()),
        b"nft_id": nftID,
        b"start": startTime,
        b"end": endTime,
        b"reserve_amount": reserve,
        b"min_bid_inc": increment,
        b"num_bids": 1,
        b"bid_amount": bidAmount,
        b"bid_account": encoding.decode_address(bidder.getAddress()),
    }

    assert actualState == expectedState

    actualBalances = getBalances(client, getAppAddress(appID))
    expectedBalances = {0: 2 * 100_000 + 2 * 1_000 + bidAmount, nftID: nftAmount}

    assert actualBalances == expectedBalances


def test_second_bid():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 60  # end time is 1 minute after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    bidder1 = getTemporaryAccount(client)
    bidder2 = getTemporaryAccount(client)

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < startTime:
        sleep(startTime - lastRoundTime)

    bid1Amount = 500_000  # 0.5 Algos
    placeBid(client=client, appID=appID, bidder=bidder1, bidAmount=bid1Amount)

    bidder1AlgosBefore = getBalances(client, bidder1.getAddress())[0]

    with pytest.raises(Exception):
        bid2Amount = bid1Amount + 1_000  # increase is less than min increment amount
        placeBid(
            client=client,
            appID=appID,
            bidder=bidder2,
            bidAmount=bid2Amount,
        )

    bid2Amount = bid1Amount + increment
    placeBid(client=client, appID=appID, bidder=bidder2, bidAmount=bid2Amount)

    actualState = getAppGlobalState(client, appID)
    expectedState = {
        b"seller": encoding.decode_address(seller.getAddress()),
        b"nft_id": nftID,
        b"start": startTime,
        b"end": endTime,
        b"reserve_amount": reserve,
        b"min_bid_inc": increment,
        b"num_bids": 2,
        b"bid_amount": bid2Amount,
        b"bid_account": encoding.decode_address(bidder2.getAddress()),
    }

    assert actualState == expectedState

    actualAppBalances = getBalances(client, getAppAddress(appID))
    expectedAppBalances = {0: 2 * 100_000 + 2 * 1_000 + bid2Amount, nftID: nftAmount}

    assert actualAppBalances == expectedAppBalances

    bidder1AlgosAfter = getBalances(client, bidder1.getAddress())[0]

    # bidder1 should receive a refund of their bid, minus the txn fee
    assert bidder1AlgosAfter - bidder1AlgosBefore >= bid1Amount - 1_000


def test_close_before_start():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 5 * 60  # start time is 5 minutes in the future
    endTime = startTime + 60  # end time is 1 minute after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    _, lastRoundTime = getLastBlockTimestamp(client)
    assert lastRoundTime < startTime

    closeAuction(client, appID, seller)

    actualAppBalances = getBalances(client, getAppAddress(appID))
    expectedAppBalances = {0: 0}

    assert actualAppBalances == expectedAppBalances

    sellerNftBalance = getBalances(client, seller.getAddress())[nftID]
    assert sellerNftBalance == nftAmount


def test_close_no_bids():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 30  # end time is 30 seconds after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < endTime:
        sleep(endTime - lastRoundTime)

    closeAuction(client, appID, seller)

    actualAppBalances = getBalances(client, getAppAddress(appID))
    expectedAppBalances = {0: 0}

    assert actualAppBalances == expectedAppBalances

    sellerNftBalance = getBalances(client, seller.getAddress())[nftID]
    assert sellerNftBalance == nftAmount


def test_close_reserve_not_met():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 30  # end time is 30 seconds after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    bidder = getTemporaryAccount(client)

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < startTime:
        sleep(startTime - lastRoundTime)

    bidAmount = 500_000  # 0.5 Algos
    placeBid(client=client, appID=appID, bidder=bidder, bidAmount=bidAmount)

    bidderAlgosBefore = getBalances(client, bidder.getAddress())[0]

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < endTime:
        sleep(endTime - lastRoundTime)

    closeAuction(client, appID, seller)

    actualAppBalances = getBalances(client, getAppAddress(appID))
    expectedAppBalances = {0: 0}

    assert actualAppBalances == expectedAppBalances

    bidderAlgosAfter = getBalances(client, bidder.getAddress())[0]

    # bidder should receive a refund of their bid, minus the txn fee
    assert bidderAlgosAfter - bidderAlgosBefore >= bidAmount - 1_000

    sellerNftBalance = getBalances(client, seller.getAddress())[nftID]
    assert sellerNftBalance == nftAmount


def test_close_reserve_met():
    client = getAlgodClient()

    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)

    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)

    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 30  # end time is 30 seconds after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo

    appID = createAuctionApp(
        client=client,
        sender=creator,
        seller=seller.getAddress(),
        nftID=nftID,
        startTime=startTime,
        endTime=endTime,
        reserve=reserve,
        minBidIncrement=increment,
    )

    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )

    sellerAlgosBefore = getBalances(client, seller.getAddress())[0]

    bidder = getTemporaryAccount(client)

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < startTime:
        sleep(startTime - lastRoundTime)

    bidAmount = reserve
    placeBid(client=client, appID=appID, bidder=bidder, bidAmount=bidAmount)

    optInToAsset(client, nftID, bidder)

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < endTime:
        sleep(endTime - lastRoundTime)

    closeAuction(client, appID, seller)

    actualAppBalances = getBalances(client, getAppAddress(appID))
    expectedAppBalances = {0: 0}

    assert actualAppBalances == expectedAppBalances

    bidderNftBalance = getBalances(client, bidder.getAddress())[nftID]

    assert bidderNftBalance == nftAmount

    actualSellerBalances = getBalances(client, seller.getAddress())

    assert len(actualSellerBalances) == 2
    # seller should receive the bid amount, minus the txn fee
    assert actualSellerBalances[0] >= sellerAlgosBefore + bidAmount - 1_000
    assert actualSellerBalances[nftID] == 0
