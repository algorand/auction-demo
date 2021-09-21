# Algorand Auction Demo

This demo is an on-chain NFT auction using smart contracts on the Algorand blockchain.

## Usage

The file `auction/operations.py` provides a set of functions that can be used to create and interact
with auctions. See that file for documentation.

## Development Setup

This repo requires Python 3.6 or higher. We recommend you use a Python virtual environment to install
the required dependencies.

Set up venv (one time):
 * `python3 -m venv venv`

Active venv:
 * `. venv/bin/activate` (if your shell is bash/zsh)
 * `. venv/bin/activate.fish` (if your shell is fish)

Install dependencies:
* `pip install -r requirements.txt`

Run tests:
* `pytest`

Format code:
* `black .`
