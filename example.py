from time import time, sleep

from algosdk import account, encoding
from algosdk.logic import get_application_address
from auction.operations import createAuctionApp, setupAuctionApp, placeBid, closeAuction
from auction.util import (
    getBalances,
    getAppGlobalState,
    getLastBlockTimestamp,
)
from auction.testing.setup import getAlgodClient
from auction.testing.resources import (
    getTemporaryAccount,
    optInToAsset,
    createDummyAsset,
)


def simple_auction():
    client = getAlgodClient()

    print("Generating temporary accounts...")
    creator = getTemporaryAccount(client)
    seller = getTemporaryAccount(client)
    bidder = getTemporaryAccount(client)

    print("Alice (seller account):", seller.getAddress())
    print("Bob (auction creator account):", creator.getAddress())
    print("Carla (bidder account)", bidder.getAddress(), "\n")

    print("Alice is generating an example NFT...")
    nftAmount = 1
    nftID = createDummyAsset(client, nftAmount, seller)
    print("The NFT ID is", nftID)
    print("Alice's balances:", getBalances(client, seller.getAddress()), "\n")

    startTime = int(time()) + 10  # start time is 10 seconds in the future
    endTime = startTime + 30  # end time is 30 seconds after start
    reserve = 1_000_000  # 1 Algo
    increment = 100_000  # 0.1 Algo
    print("Bob is creating an auction that lasts 30 seconds to auction off the NFT...")
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
    print(
        "Done. The auction app ID is",
        appID,
        "and the escrow account is",
        get_application_address(appID),
        "\n",
    )

    print("Alice is setting up and funding NFT auction...")
    setupAuctionApp(
        client=client,
        appID=appID,
        funder=creator,
        nftHolder=seller,
        nftID=nftID,
        nftAmount=nftAmount,
    )
    print("Done\n")

    sellerBalancesBefore = getBalances(client, seller.getAddress())
    sellerAlgosBefore = sellerBalancesBefore[0]
    print("Alice's balances:", sellerBalancesBefore)

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < startTime + 5:
        sleep(startTime + 5 - lastRoundTime)
    actualAppBalancesBefore = getBalances(client, get_application_address(appID))
    print("Auction escrow balances:", actualAppBalancesBefore, "\n")

    bidAmount = reserve
    bidderBalancesBefore = getBalances(client, bidder.getAddress())
    bidderAlgosBefore = bidderBalancesBefore[0]
    print("Carla wants to bid on NFT, her balances:", bidderBalancesBefore)
    print("Carla is placing bid for", bidAmount, "microAlgos")

    placeBid(client=client, appID=appID, bidder=bidder, bidAmount=bidAmount)

    print("Carla is opting into NFT with ID", nftID)

    optInToAsset(client, nftID, bidder)

    print("Done\n")

    _, lastRoundTime = getLastBlockTimestamp(client)
    if lastRoundTime < endTime + 5:
        waitTime = endTime + 5 - lastRoundTime
        print("Waiting {} seconds for the auction to finish\n".format(waitTime))
        sleep(waitTime)

    print("Alice is closing out the auction\n")
    closeAuction(client, appID, seller)

    actualAppBalances = getBalances(client, get_application_address(appID))
    expectedAppBalances = {0: 0}
    print("The auction escrow now holds the following:", actualAppBalances)
    assert actualAppBalances == expectedAppBalances

    bidderNftBalance = getBalances(client, bidder.getAddress())[nftID]
    assert bidderNftBalance == nftAmount

    actualSellerBalances = getBalances(client, seller.getAddress())
    print("Alice's balances after auction: ", actualSellerBalances, " Algos")
    actualBidderBalances = getBalances(client, bidder.getAddress())
    print("Carla's balances after auction: ", actualBidderBalances, " Algos")
    assert len(actualSellerBalances) == 2
    # seller should receive the bid amount, minus the txn fee
    assert actualSellerBalances[0] >= sellerAlgosBefore + bidAmount - 1_000
    assert actualSellerBalances[nftID] == 0


simple_auction()
