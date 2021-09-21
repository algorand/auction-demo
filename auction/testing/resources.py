from typing import List
from random import choice, randint

from algosdk.v2client.algod import AlgodClient
from algosdk.future import transaction
from algosdk import account

from ..account import Account
from ..util import PendingTxnResponse, waitForTransaction
from .setup import getGenesisAccounts


def payAccount(
    client: AlgodClient, sender: Account, to: str, amount: int
) -> PendingTxnResponse:
    txn = transaction.PaymentTxn(
        sender=sender.getAddress(),
        receiver=to,
        amt=amount,
        sp=client.suggested_params(),
    )
    signedTxn = txn.sign(sender.getPrivateKey())

    client.send_transaction(signedTxn)
    return waitForTransaction(client, signedTxn.get_txid())


FUNDING_AMOUNT = 100_000_000


def fundAccount(
    client: AlgodClient, address: str, amount: int = FUNDING_AMOUNT
) -> PendingTxnResponse:
    fundingAccount = choice(getGenesisAccounts())
    return payAccount(client, fundingAccount, address, amount)


accountList: List[Account] = []


def getTemporaryAccount(client: AlgodClient) -> Account:
    global accountList

    if len(accountList) == 0:
        sks = [account.generate_account()[0] for i in range(16)]
        accountList = [Account(sk) for sk in sks]

        genesisAccounts = getGenesisAccounts()
        suggestedParams = client.suggested_params()

        txns: List[transaction.Transaction] = []
        for i, a in enumerate(accountList):
            fundingAccount = genesisAccounts[i % len(genesisAccounts)]
            txns.append(
                transaction.PaymentTxn(
                    sender=fundingAccount.getAddress(),
                    receiver=a.getAddress(),
                    amt=FUNDING_AMOUNT,
                    sp=suggestedParams,
                )
            )

        txns = transaction.assign_group_id(txns)
        signedTxns = [
            txn.sign(genesisAccounts[i % len(genesisAccounts)].getPrivateKey())
            for i, txn in enumerate(txns)
        ]

        client.send_transactions(signedTxns)

        waitForTransaction(client, signedTxns[0].get_txid())

    return accountList.pop()


def optInToAsset(
    client: AlgodClient, assetID: int, account: Account
) -> PendingTxnResponse:
    txn = transaction.AssetOptInTxn(
        sender=account.getAddress(),
        index=assetID,
        sp=client.suggested_params(),
    )
    signedTxn = txn.sign(account.getPrivateKey())

    client.send_transaction(signedTxn)
    return waitForTransaction(client, signedTxn.get_txid())


def createDummyAsset(client: AlgodClient, total: int, account: Account = None) -> int:
    if account is None:
        account = getTemporaryAccount(client)

    randomNumber = randint(0, 999)
    # this random note reduces the likelihood of this transaction looking like a duplicate
    randomNote = bytes(randint(0, 255) for _ in range(20))

    txn = transaction.AssetCreateTxn(
        sender=account.getAddress(),
        total=total,
        decimals=0,
        default_frozen=False,
        manager=account.getAddress(),
        reserve=account.getAddress(),
        freeze=account.getAddress(),
        clawback=account.getAddress(),
        unit_name=f"D{randomNumber}",
        asset_name=f"Dummy {randomNumber}",
        url=f"https://dummy.asset/{randomNumber}",
        note=randomNote,
        sp=client.suggested_params(),
    )
    signedTxn = txn.sign(account.getPrivateKey())

    client.send_transaction(signedTxn)

    response = waitForTransaction(client, signedTxn.get_txid())
    assert response.assetIndex is not None and response.assetIndex > 0
    return response.assetIndex
