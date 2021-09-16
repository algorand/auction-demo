# Algorand Auction Demo

This demo is an on-chain auction built with smart contracts on the Algorand blockchain.

## Development Setup

This repo request Python 3.6 or higher. We recommend you use a Python virtual environment to install
the required dependencies.

Setup venv (one time):
 * `python3 -m venv venv`

Active venv:
 * `. venv/bin/activate` (if your shell is bash/zsh)
 * `. venv/bin/activate.fish` (if your shell is fish)

Install dependencies:
* `pip install -r requirements.txt`

Type checking using mypy:
* `mypy pyteal`

Run tests:
* `pytest`

Format code:
* `black .`
