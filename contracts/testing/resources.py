from typing import List, Tuple, Dict, Any, Optional, Union
from random import choice, randint
from base64 import b64decode

from algosdk.v2client.algod import AlgodClient
from algosdk.future import transaction
from algosdk import account, encoding

from pyteal import compileTeal, Mode, Expr

from .account import Account
from .setup import getGenesisAccounts


class PendingTxnResponse:
    def __init__(self, response: Dict[str, Any]) -> None:
        self.poolError: str = response["pool-error"]
        self.txn: Dict[str, Any] = response["txn"]

        self.applicationIndex: Optional[int] = response.get("application-index")
        self.assetIndex: Optional[int] = response.get("asset-index")
        self.closeRewards: Optional[int] = response.get("close-rewards")
        self.closingAmount: Optional[int] = response.get("closing-amount")
        self.confirmedRound: Optional[int] = response.get("confirmed-round")
        self.globalStateDelta: Optional[Any] = response.get("global-state-delta")
        self.localStateDelta: Optional[Any] = response.get("local-state-delta")
        self.receiverRewards: Optional[int] = response.get("receiver-rewards")
        self.senderRewards: Optional[int] = response.get("sender-rewards")

        self.innerTxns: List[Any] = response.get("inner-txns", [])
        self.logs: List[bytes] = [b64decode(l) for l in response.get("logs", [])]


def waitForTransaction(
    client: AlgodClient, txID: str, timeout: int = 10
) -> PendingTxnResponse:
    lastStatus = client.status()
    lastRound = lastStatus["last-round"]
    startRound = lastRound

    while lastRound < startRound + timeout:
        pending_txn = client.pending_transaction_info(txID)

        if pending_txn.get("confirmed-round", 0) > 0:
            return PendingTxnResponse(pending_txn)

        if pending_txn["pool-error"]:
            raise Exception("Pool error: {}".format(pending_txn["pool-error"]))

        lastStatus = client.status_after_block(lastRound + 1)

        lastRound += 1

    raise Exception(
        "Transaction {} not confirmed after {} rounds".format(txID, timeout)
    )


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


def fullyCompileContract(client: AlgodClient, contract: Expr) -> bytes:
    teal = compileTeal(contract, mode=Mode.Application, version=5)
    response = client.compile(teal)
    return b64decode(response["result"])


def decodeState(stateArray: List[Any]) -> Dict[bytes, Union[int, bytes]]:
    state: Dict[bytes, Union[int, bytes]] = dict()

    for pair in stateArray:
        key = b64decode(pair["key"])

        value = pair["value"]
        valueType = value["type"]

        if valueType == 2:
            # value is uint64
            value = value.get("uint", 0)
        elif valueType == 1:
            # value is byte array
            value = b64decode(value.get("bytes", ""))
        else:
            raise Exception(f"Unexpected state type: {valueType}")

        state[key] = value

    return state


def getAppGlobalState(
    client: AlgodClient, appID: int
) -> Dict[bytes, Union[int, bytes]]:
    appInfo = client.application_info(appID)
    return decodeState(appInfo["params"]["global-state"])


def getAppAddress(appID: int) -> str:
    toHash = b"appID" + appID.to_bytes(8, "big")
    return encoding.encode_address(encoding.checksum(toHash))


def getBalances(client: AlgodClient, account: str) -> Dict[int, int]:
    balances: Dict[int, int] = dict()

    accountInfo = client.account_info(account)

    # set key 0 to Algo balance
    balances[0] = accountInfo["amount"]

    assets: List[Dict[str, Any]] = accountInfo.get("assets", [])
    for assetHolding in assets:
        assetID = assetHolding["asset-id"]
        amount = assetHolding["amount"]
        balances[assetID] = amount

    return balances


def getLastBlockTimestamp(client: AlgodClient) -> Tuple[int, int]:
    status = client.status()
    lastRound = status["last-round"]
    block = client.block_info(lastRound)
    timestamp = block["block"]["ts"]

    return block, timestamp
